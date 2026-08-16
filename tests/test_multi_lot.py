"""§9.2 멀티 lot 분석 — lot 선택·커버리지·멀티 lot 탭·lot 심볼·기준 lot.

`design-plans/multi-lot-analysis.md`의 결정을 코드로 고정한다.

가장 중요한 불변식은 **"lot을 고르지 않으면 예전과 똑같이 동작한다"**이다.
lot 필터는 읽는 SQL 한가운데에 들어가므로, 안 골랐을 때 글자 하나라도 달라지면
지금까지 정상 동작하던 모든 DB의 동작이 바뀐다.
"""
from __future__ import annotations

import os
from datetime import datetime

import duckdb
import polars as pl
import pytest

from etreport.data import compat, db, loader
from etreport.model import coverage
from etreport.model.state import AppState

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = {"line_id": "L1", "root_lot_id": "PA100", "wafer_id": "01",
        "chip_x_pos": 1, "chip_y_pos": 1, "temperature": 25.0,
        "step_id": "M2", "step_seq": 1, "total_site_cnt": 9,
        "tkout_time": datetime(2026, 8, 4, 9, 0)}


@pytest.fixture
def multi_db(tmp_path):
    """lot 3개 — 구성이 일부러 다르다.

    PA100: wafer 2장 · item 2종(Vt, Idsat) · step 2종
    PB200: wafer 2장 · item 2종 · step 2종        (PA100과 같은 모양)
    PC300: wafer 1장 · item 1종(Vt) · step 1종    (item 결손 + 조건 불일치)
    """
    rows = []
    for lot, wafers, items, steps in (
            ("PA100", ("01", "02"), ("Vt", "Idsat"), ("M2", "M5")),
            ("PB200", ("01", "02"), ("Vt", "Idsat"), ("M2", "M5")),
            ("PC300", ("01",), ("Vt",), ("M2",))):
        for wafer in wafers:
            for step in steps:
                for item in items:
                    for chip in (1, 2):
                        rows.append({**BASE, "root_lot_id": lot,
                                     "wafer_id": wafer, "step_id": step,
                                     "chip_x_pos": chip, "item_id": item,
                                     "et_value": 0.4})
    p = tmp_path / "multi.parquet"
    pl.DataFrame(rows).write_parquet(p)
    out = tmp_path / "et.duckdb"
    store = db.Store(out)
    try:
        db.pivot_and_load(store, [p])
    finally:
        store.close()
    return str(out)


def _profile(db_path: str):
    con = loader.open_readonly(db_path)
    try:
        return compat.profile(con, compat.pick_table(con))
    finally:
        con.close()


# ── §3-1 SQL에서 좁힌다 ──────────────────────────────────────
def test_no_lots_means_identical_sql(multi_db):
    """★ lot을 고르지 않으면 **글자 하나까지** 예전과 같은 SQL이다."""
    p = _profile(multi_db)
    base = compat.select_sql(p)

    assert compat.select_sql(p, lots=None) == base
    assert compat.select_sql(p, lots=[]) == base
    assert compat.wafer_index_sql(p) == compat.wafer_index_sql(p, None)
    assert "WHERE" not in base.upper().replace("WHERE ROW", "")


@pytest.mark.parametrize("is_long,merge", [(True, True), (False, False),
                                           (False, True)])
def test_lot_filter_reaches_every_branch(is_long, merge):
    """★ long·wide × 병합·비병합 — 어느 갈래로 가도 IN이 들어간다."""
    cols = {"root_lot_id": "VARCHAR", "wafer_id": "VARCHAR",
            "chip_x_pos": "INTEGER", "chip_y_pos": "INTEGER",
            "temperature": "DOUBLE", "step_id": "VARCHAR",
            "total_site_cnt": "INTEGER", "tkout_time": "TIMESTAMP"}
    if merge:
        cols["step_seq"] = "INTEGER"
    if is_long:
        cols |= {"item_id": "VARCHAR", "et_value": "DOUBLE"}
    else:
        cols |= {"Vt": "DOUBLE"}
    p = compat.profile.__wrapped__ if False else None  # noqa: F841 (문서용)
    prof = compat.TableProfile(table="et_data", columns=cols)
    lower = {c.lower(): c for c in cols}
    for role, cands in compat.ROLE_ALIASES.items():
        for cand in cands:
            if cand in lower:
                prof.roles[role] = lower[cand]
                break
    prof.is_long = "item" in prof.roles and "value" in prof.roles
    if not prof.is_long:
        prof.items = ["Vt"]

    sql = compat.select_sql(prof, lots=["PA100", "PB200"])
    assert "\"root_lot_id\" IN ('PA100', 'PB200')" in sql
    # WHERE는 QUALIFY보다 앞이어야 한다 — retest 판정이 좁힌 범위에서 돌게
    if "QUALIFY" in sql:
        assert sql.index("IN ('PA100'") < sql.index("QUALIFY")


def test_lot_filter_quotes_and_dedups():
    prof = compat.TableProfile(table="t", columns={"lot": "VARCHAR"},
                               roles={"lot": "lot"})
    got = compat.lot_filter(prof, ["A'B", "C", "C"])

    assert got == """ WHERE "lot" IN ('A''B', 'C')"""
    assert compat.lot_filter(prof, []) == ""
    # lot 컬럼을 못 찾은 스키마에서는 조용히 좁히지 않는다
    assert compat.lot_filter(compat.TableProfile("t", {}), ["A"]) == ""


def test_load_state_narrows_to_selected_lots(multi_db, appdata):
    """★ 고른 lot만 프레임에 들어오고, 요약이 3/12 꼴로 말해 준다."""
    st = AppState()
    st.lots_all = ["PA100", "PB200", "PC300"]
    msg = loader.load_state(st, multi_db, lots=["PA100"])

    assert sorted(set(st.data["lot"])) == ["PA100"]
    assert "lot 1/3" in msg

    all_msg = loader.load_state(AppState(), multi_db)
    assert "lot 3" in all_msg and "/" not in all_msg.split("lot ")[1][:4]


def test_lot_index_lists_lots_and_wafer_counts(multi_db):
    idx = loader.lot_index(multi_db)

    assert idx["lot"].to_list() == ["PA100", "PB200", "PC300"]
    assert idx["wafers"].to_list() == [2, 2, 1]
    assert loader.lot_index("/없는/파일.duckdb").is_empty()


def test_wafer_index_from_db_narrows(multi_db):
    idx = loader.wafer_index_from_db(multi_db, ["PC300"])

    assert sorted(set(idx["lot"])) == ["PC300"]


# ── §3-1 설정 저장 ───────────────────────────────────────────
def test_lot_selection_survives_settings_roundtrip(appdata):
    from etreport.config.settings import Settings
    s = Settings.defaults()
    s.lot_selections["/data/et.duckdb"] = ["PA100", "PB200"]
    s.save()

    back = Settings.load()
    assert back.lot_selections["/data/et.duckdb"] == ["PA100", "PB200"]


def test_settings_without_lot_selections_still_loads(appdata):
    """★ 이 필드를 모르는 예전 설정 파일도 그대로 열린다."""
    import json

    from etreport.config.settings import Settings
    from etreport.paths import settings_file
    settings_file().write_text(json.dumps(
        {"analysis_configs": [{"name": "옛것"}], "lot_selections": "망가짐"}),
        encoding="utf-8")

    s = Settings.load()
    assert s.lot_selections == {}
    assert s.analysis_config("옛것") is not None


# ── §3-3 커버리지 ────────────────────────────────────────────
def _wide(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def test_coverage_picks_base_lot_and_finds_missing_items(multi_db, appdata):
    st = AppState()
    loader.load_state(st, multi_db)
    rep = coverage.build(st)

    assert rep.base_lot in ("PA100", "PB200")     # item이 가장 많은 lot
    pc = next(r for r in rep.rows if r.lot == "PC300")
    assert pc.missing_items == ["Idsat"]
    assert pc.wafers == 1
    assert "M5" in " ".join(pc.cond_missing)      # PC300은 M5를 안 지났다
    assert rep.has_warnings()
    assert any("PC300" in ln for ln in rep.lines())


def test_coverage_base_lot_tie_breaks_by_name():
    rows = [{"key": f"{lot}{w}", "lot": lot, "wafer": w, "gid": "",
             "step": "M2", "temp": "25", "site": "9", "Vt": 1.0}
            for lot in ("PB", "PA") for w in ("01", "02", "03")]
    rep = coverage.build_frame(_wide(rows), ["Vt"])

    assert rep.base_lot == "PA"                   # item 수가 같으면 이름 순
    assert not rep.has_warnings()


def test_coverage_point_ratio_tolerance():
    """★ wafer당 포인트 **중앙값**이 기준의 ±20%를 벗어나면 알린다."""
    rows = []
    for lot, per in (("PA", 10), ("PB", 9), ("PC", 4)):
        for w in ("01", "02"):
            rows += [{"key": f"{lot}{w}{i}", "lot": lot, "wafer": w, "gid": "",
                      "step": "M2", "temp": "25", "site": "9", "Vt": 1.0}
                     for i in range(per)]
    rep = coverage.build_frame(_wide(rows), ["Vt"])
    got = {r.lot: r for r in rep.rows}

    assert got["PA"].is_base
    assert not got["PB"].point_off               # 0.9배 — 허용 범위
    assert got["PC"].point_off                   # 0.4배 — 알린다
    assert any("0.40배" in ln for ln in rep.lines())


def test_coverage_median_ignores_one_short_wafer():
    """★ 평균이 아니라 중앙값 — 덜 측정된 wafer 한 장이 lot을 경고로 만들지 않는다."""
    rows = []
    for lot in ("PA", "PB"):
        for w in ("01", "02", "03"):
            n = 1 if (lot == "PB" and w == "03") else 10
            rows += [{"key": f"{lot}{w}{i}", "lot": lot, "wafer": w, "gid": "",
                      "step": "M2", "temp": "25", "site": "9", "Vt": 1.0}
                     for i in range(n)]
    rep = coverage.build_frame(_wide(rows), ["Vt"])

    assert not next(r for r in rep.rows if r.lot == "PB").point_off


def test_coverage_is_silent_for_single_lot():
    rows = [{"key": f"{w}", "lot": "PA", "wafer": w, "gid": "", "step": "M2",
             "temp": "25", "site": "9", "Vt": 1.0} for w in ("01", "02")]
    rep = coverage.build_frame(_wide(rows), ["Vt"])

    assert rep.lines() == []


def test_coverage_lines_reach_load_report(multi_db, appdata):
    """★ [적용] 결과에 커버리지가 실린다. **제외(warnings)가 아니라 notes**다 —
    버린 것이 없는데 '제외 N건'으로 세면 안 되고, 볼 때마다 모달이 뜨면 안 된다."""
    from etreport.config.settings import AnalysisConfig
    from etreport.model.session import apply_config
    st = AppState()
    rep = apply_config(st, AnalysisConfig(name="t", db_path=multi_db))

    assert rep.ok
    assert any(n.startswith("[커버리지]") for n in rep.notes)
    assert not any(w.startswith("[커버리지]") for w in rep.warnings)
    body = rep.text()
    assert f"확인 {len(rep.notes)}건:" in body
    assert "제외 4건:" not in body           # 제외 묶음으로 세지 않는다


def test_coverage_tsv_has_every_missing_item():
    from etreport.ui.widgets.coverage_dialog import report_tsv
    rows = [{"key": f"{lot}{w}", "lot": lot, "wafer": w, "gid": "",
             "step": "M2", "temp": "25", "site": "9",
             **{f"I{i}": (None if lot == "PB" else 1.0) for i in range(12)}}
            for lot in ("PA", "PB") for w in ("01",)]
    rep = coverage.build_frame(_wide(rows), [f"I{i}" for i in range(12)])
    tsv = report_tsv(rep)

    assert tsv.splitlines()[0].startswith("lot\t")
    assert all(f"I{i}" in tsv for i in range(12))     # 표는 접어도 복사는 전부


# ── §5 lot 심볼 분화 ─────────────────────────────────────────
def _plot_frames() -> dict[str, pl.DataFrame]:
    rows = {"g0": [], "g1": []}
    for gid, lots in (("g0", ("PA", "PB")), ("g1", ("PB", "PC"))):
        for lot in lots:
            for i in range(3):
                rows[gid].append({"key": f"{gid}{lot}{i}", "lot": lot,
                                  "wafer": f"0{i}", "gid": gid,
                                  "X": 1.0 + i, "Y": 2.0 + i})
    return {k: pl.DataFrame(v) for k, v in rows.items()}


def test_lot_markers_are_stable_across_groups():
    """★ 같은 lot은 어느 그룹·어느 plot에서도 같은 모양이다."""
    from etreport.render import mpl_renderer as mr
    data = _plot_frames()
    marks = mr.lot_markers(data)

    assert sorted(marks) == ["PA", "PB", "PC"]
    assert len(set(marks.values())) == 3
    # 그룹 하나만 넘겨도 그 lot의 모양은 전체 기준과 같아야 한다 → 같은 함수를
    # 전체 data로 한 번만 부르는 것이 규칙
    assert mr.lot_markers({"g0": data["g0"]})["PA"] == marks["PA"]


def test_lot_parts_off_is_unchanged():
    """★ 토글이 꺼져 있으면 프레임을 나누지 않는다(예전 그림 그대로)."""
    from etreport.model.specs import GroupStyle
    from etreport.render import mpl_renderer as mr
    st = GroupStyle(gid="g0", name="A", color="#000", symbol="s", size=6)
    df = _plot_frames()["g0"]

    parts = mr._lot_parts(df, False, {}, st)
    assert len(parts) == 1
    assert parts[0][1] == mr.MARKER["s"] and parts[0][2] == "A"


def test_lot_parts_on_splits_and_labels():
    from etreport.model.specs import GroupStyle
    from etreport.render import mpl_renderer as mr
    st = GroupStyle(gid="g0", name="A", color="#000", symbol="s", size=6)
    data = _plot_frames()
    parts = mr._lot_parts(data["g0"], True, mr.lot_markers(data), st)

    assert [p[2] for p in parts] == ["A (PA)", "A (PB)"]
    assert len({p[1] for p in parts}) == 2        # lot이 모양을 정한다
    assert all(p[1] != mr.MARKER["s"] or True for p in parts)


def test_renderer_legend_carries_lot():
    from etreport.data.reformatter import Reformatter
    from etreport.model.specs import GroupStyle, PlotSpec
    from etreport.render import mpl_renderer as mr
    data = _plot_frames()
    styles = [GroupStyle(gid="g0", name="A", color="#111", symbol="o", size=6),
              GroupStyle(gid="g1", name="B", color="#222", symbol="s", size=6)]
    spec = PlotSpec(x="X", y="Y", title="t", mode="site")

    off = mr.render(spec, data, styles, Reformatter(), [], (5, 4))
    labels_off = off.axes[0].get_legend_handles_labels()[1]
    on = mr.render(spec, data, styles, Reformatter(), [], (5, 4),
                   lot_split=True)
    labels_on = on.axes[0].get_legend_handles_labels()[1]

    assert labels_off == ["A", "B"]
    assert labels_on == ["A (PA)", "A (PB)", "B (PB)", "B (PC)"]


# ── §5 PPT 표 lot 경계 분할 ──────────────────────────────────
def _table(header) -> object:
    from etreport.render.pptgen import TableData
    n = sum(len(ws) for _lot, ws in header)
    return TableData("CAT1", header,
                     [{"cats": ["a"], "item": "Vt",
                       "values": list(range(n)), "offspec": [False] * n}],
                     cat_names=["구분"])


def test_split_table_single_lot_unchanged():
    """★ lot이 하나면 예전 그대로 — 25장이면 12/12/1."""
    from etreport.render.pptgen import split_table
    parts = split_table(_table([("PA", [f"{i:02d}" for i in range(1, 26)])]))

    assert len(parts) == 3
    assert [sum(len(w) for _l, w in p.header_lots) for p in parts] == [12, 12, 1]
    assert parts[0].cat_names == ["구분"]          # 라벨 열 이름이 살아 있다


def test_split_table_cuts_at_lot_boundary():
    """★ lot 2개면 각각 5장이어도 두 장으로 나뉜다."""
    from etreport.render.pptgen import split_table
    parts = split_table(_table([("PA", ["01", "02", "03", "04", "05"]),
                                ("PB", ["01", "02", "03", "04", "05"])]))

    assert len(parts) == 2
    assert [p.header_lots[0][0] for p in parts] == ["PA", "PB"]
    assert all(len(p.header_lots) == 1 for p in parts)   # lot이 섞이지 않는다


def test_split_table_subdivides_a_big_lot():
    from etreport.render.pptgen import split_table
    parts = split_table(_table([("PA", [f"{i:02d}" for i in range(1, 26)]),
                                ("PB", ["01", "02", "03"])]))

    assert len(parts) == 4                        # 12 · 12 · 1 · 3
    assert [p.header_lots[0][0] for p in parts] == ["PA", "PA", "PA", "PB"]
    assert [p.rows[0]["values"][0] for p in parts] == [0, 12, 24, 25]


def test_split_table_one_lot_one_slide_stays_whole():
    from etreport.render.pptgen import split_table
    td = _table([("PA", ["01", "02"])])

    assert split_table(td) == [td]                # 나눌 이유가 없으면 그대로


def test_split_table_group_average_is_untouched():
    """★ 그룹별 평균 표는 머리글 블록이 하나뿐이라 예전과 똑같이 나뉜다."""
    from etreport.render.pptgen import split_table
    names = [f"그룹{i}" for i in range(1, 16)]

    parts = split_table(_table([("그룹", names)]))
    assert [sum(len(w) for _l, w in p.header_lots) for p in parts] == [12, 3]
    assert split_table(_table([("그룹", names[:5])]))[0].header_lots[0][0] == "그룹"


# ── §6 기준 lot → step별 baseline ────────────────────────────
def _split_frame() -> pl.DataFrame:
    """PA100은 M1=R1이 다수, M5=P9가 다수. PB200은 다른 조건이 다수."""
    return pl.DataFrame([
        {"lot": "PA100", "wafer": "01", "M1": "R1", "M5": "P9"},
        {"lot": "PA100", "wafer": "02", "M1": "R1", "M5": "P9"},
        {"lot": "PA100", "wafer": "03", "M1": "R2", "M5": "P8"},
        {"lot": "PB200", "wafer": "01", "M1": "R2", "M5": "P8"},
        {"lot": "PB200", "wafer": "02", "M1": "R2", "M5": "P8"},
    ])


def test_baseline_lot_yields_per_step_codes():
    """★ 기준 lot을 고르면 step마다 그 lot의 다수 조건이 기준이 된다."""
    from etreport.model.split import SplitMatrix
    sm = SplitMatrix.from_dataframe(_split_frame(), baseline_lot="PA100")

    assert sm.baseline_codes == {"M1": "R1", "M5": "P9"}
    assert sm.code_of("M1") == "R1" and sm.code_of("M5") == "P9"
    assert "step별" in sm.baseline_label()


def test_baseline_string_stays_backward_compatible():
    """★ 문자열 하나만 준 예전 경로는 전 step 공통 — 지금까지와 같다."""
    from etreport.model.split import SplitMatrix
    sm = SplitMatrix.from_dataframe(_split_frame(), baseline="R1")

    assert sm.baseline_codes == {}
    assert sm.code_of("M1") == "R1" and sm.code_of("M5") == "R1"
    assert sm.baseline_label() == "R1"


def test_ref_group_follows_per_step_baseline():
    """★ REF는 factor마다 그 step의 기준 코드와 맞을 때만 붙는다."""
    from etreport.model.split import SplitMatrix
    sm = SplitMatrix.from_dataframe(_split_frame(), baseline_lot="PA100")
    styles = sm.styles_for(["M1", "M5"])
    refs = [g.name for g in styles if g.ref]

    assert len(refs) == 1
    assert refs[0].startswith("R1 · P9")


def test_codes_from_lot_ignores_other_lots():
    from etreport.model.split import SplitMatrix
    codes = SplitMatrix.codes_from_lot(_split_frame(), ["M1", "M5"], "PB200")

    assert codes == {"M1": "R2", "M5": "P8"}


def test_tracking_baseline_lot_is_per_step():
    from etreport.data import fabtracking as ft
    df = pl.DataFrame([
        {"root_lot_id": "PA", "wafer_id": "01", "process_id": "M1",
         "area": "PHOTO", "reticle_id": "R1", "ppid": "x", "step_seq": 1},
        {"root_lot_id": "PA", "wafer_id": "02", "process_id": "M1",
         "area": "PHOTO", "reticle_id": "R1", "ppid": "x", "step_seq": 1},
        {"root_lot_id": "PA", "wafer_id": "03", "process_id": "M1",
         "area": "PHOTO", "reticle_id": "R2", "ppid": "x", "step_seq": 1},
        {"root_lot_id": "PB", "wafer_id": "01", "process_id": "M1",
         "area": "PHOTO", "reticle_id": "R2", "ppid": "x", "step_seq": 1},
        {"root_lot_id": "PB", "wafer_id": "02", "process_id": "M1",
         "area": "PHOTO", "reticle_id": "R2", "ppid": "x", "step_seq": 1},
    ])
    sm = ft.to_split_matrix(df, baseline_lot="PA")

    assert sm.baseline_codes == {"M1": "R1"}
    assert sm.baseline_lot == "PA"


def test_majority_code_falls_back_when_lot_missing():
    from etreport.data.fabtracking import _majority_code
    cond = pl.DataFrame([{"root_lot_id": "PA", "step_id": "M1",
                          "condition": "R1"}])

    assert _majority_code(cond, lot="없는lot") == "R1"


# ── §6 계측 lot 내 순위 ──────────────────────────────────────
#: x=0..5에 대해 상관이 **정확히 0**인 y — lot 안에서는 아무 관계가 없게 만든다
_FLAT_Y = (0.0, 2.0, 1.0, 1.0, 2.0, 0.0)


def _met_frame(lot_effect: bool) -> pl.DataFrame:
    """lot_effect=True면 lot 안에서는 관계가 없고 **lot 평균만** 다르다.

    그 경우 합친 상관은 1에 가깝지만 lot 안의 상관은 0이다 — 계측값이 ET값을
    설명하는 것이 아니라 lot이 둘 다를 밀어 올린 것뿐이다.
    """
    rows = []
    for li, lot in enumerate(("PA", "PB")):
        off = li * 100 if lot_effect else 0
        for i in range(6):
            y = off + (_FLAT_Y[i] if lot_effect else i)
            rows.append({"key": f"{lot}{i}", "lot": lot, "wafer": f"{i:02d}",
                         "gid": "", "CD": float(off + i), "Vt": float(y)})
    return pl.DataFrame(rows)


def test_top_factors_has_within_lot_columns():
    from etreport.data.metrology import top_factors
    top = top_factors(_met_frame(False), ["CD"], ["Vt"])

    assert {"r", "r_within", "lots", "score"} <= set(top.columns)
    assert top["r"][0] == pytest.approx(1.0)
    assert top["r_within"][0] == pytest.approx(1.0)   # lot 안에서도 같은 관계


def test_lot_effect_is_scored_down():
    """★ lot 평균 차이로만 생긴 상관은 순위를 밀어 올리지 못한다."""
    from etreport.data.metrology import top_factors
    top = top_factors(_met_frame(True), ["CD"], ["Vt"])

    assert abs(top["r"][0]) > 0.9                 # 합치면 강한 상관처럼 보이나
    assert top["r_within"][0] == pytest.approx(0.0)   # lot 안에서는 무관하고
    assert top["score"][0] == pytest.approx(0.0)      # 점수는 보수적인 쪽을 쓴다
    assert top["lots"][0] == 2


def test_single_lot_keeps_old_ranking():
    """★ lot이 하나면 r_within이 None이라 예전과 같은 점수다."""
    from etreport.data.metrology import top_factors
    df = _met_frame(False).filter(pl.col("lot") == "PA")
    top = top_factors(df, ["CD"], ["Vt"])

    assert top["lots"][0] == 1
    assert top["r_within"][0] is None
    assert top["score"][0] == pytest.approx(abs(top["r"][0]))


def test_lot_effect_flag():
    from etreport.render.pptgen import _lot_effect

    assert _lot_effect(0.9, -0.8)                 # 부호가 뒤집힘
    assert _lot_effect(0.9, 0.2)                  # 크기가 두 배 넘게 차이
    assert not _lot_effect(0.9, 0.85)
    assert not _lot_effect(0.9, None)             # lot이 하나면 비교하지 않는다


# ── §4 멀티 lot 탭 ───────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _dialog(state, db_path=""):
    from etreport.ui.widgets.group_dialog import GroupDialog
    return GroupDialog(state, None, db_path=db_path)


def test_multi_tab_lists_lot_and_wafer(qapp, multi_db, appdata):
    """★ 멀티 lot 탭은 lot을 앞에 붙여 wafer를 가리킨다."""
    dlg = _dialog(AppState(), db_path=multi_db)

    assert [dlg.m_lots.item(i).text() for i in range(dlg.m_lots.count())] == [
        "PA100", "PB200", "PC300"]
    texts = [dlg.m_list_pool.item(i).text()
             for i in range(dlg.m_list_pool.count())]
    assert "PA100 · 01" in texts and "PC300 · 01" in texts
    assert dlg.m_list_pool.item(0).data(0x0100) == ("PA100", "01")  # UserRole


def test_multi_filters_show_partial_lot_coverage(qapp, multi_db, appdata):
    """★ 일부 lot에만 있는 값은 `(2/3 lot)`으로 표시된다."""
    dlg = _dialog(AppState(), db_path=multi_db)
    cmb = dlg.m_filters["step"]
    labels = [cmb.itemText(i) for i in range(cmb.count())]

    assert labels[0] == "전체"
    assert "M2" in labels                          # 세 lot 모두에 있다
    assert any(t.startswith("M5") and "2/3 lot" in t for t in labels)
    # 라벨이 아니라 값으로 고른다
    assert [cmb.itemData(i) for i in range(cmb.count())] == [None, "M2", "M5"]


def test_multi_assignment_range_checkbox(qapp, multi_db, appdata):
    """★ 체크하면 조건까지, 안 하면 전체 범위로 배정된다."""
    from etreport.model.specs import GroupStyle
    st = AppState()
    st.groups = [GroupStyle(gid="g0", name="A", color="#000", symbol="o",
                            size=6)]
    dlg = _dialog(st, db_path=multi_db)
    dlg.m_filters["step"].setCurrentIndex(1)       # M2

    dlg.m_chk_range.setChecked(False)
    dlg._m_assign([("PA100", "01")], "g0")
    assert ("PA100", "01", None, None, None) in st.manual_groups

    st.manual_groups.clear()
    dlg.m_chk_range.setChecked(True)
    dlg._m_assign([("PA100", "01")], "g0")
    assert ("PA100", "01", "M2", None, None) in st.manual_groups


def test_multi_auto_group_by_lot(qapp, multi_db, appdata):
    st = AppState()
    dlg = _dialog(st, db_path=multi_db)
    n = dlg.auto_group("lot", ["PA100", "PB200"])

    assert n == 2
    assert [g.name for g in st.groups] == ["PA100", "PB200"]
    assert all(k[0] in ("PA100", "PB200") for k in st.manual_groups)


def test_multi_auto_group_by_wafer_names_include_lot(qapp, multi_db, appdata):
    st = AppState()
    dlg = _dialog(st, db_path=multi_db)
    dlg.auto_group("wafer", ["PA100", "PC300"])

    assert [g.name for g in st.groups] == ["PA100·01", "PA100·02", "PC300·01"]


def test_factor_mode_bakes_split_assignment(qapp, multi_db, appdata):
    """★ split factor별은 실험 조건의 배정을 manual_groups로 굽는다 —
    그래야 손으로 고친 것이 [적용] 뒤에도 살아남는다."""
    from etreport.model.split import SplitMatrix
    st = AppState()
    st.split = SplitMatrix.from_dataframe(pl.DataFrame([
        {"lot": "PA100", "wafer": "01", "M1": "Base"},
        {"lot": "PA100", "wafer": "02", "M1": "Hi"},
        {"lot": "PB200", "wafer": "01", "M1": "Base"},
        {"lot": "PB200", "wafer": "02", "M1": "Hi"},
    ]))
    st.factors = ["M1"]
    dlg = _dialog(st, db_path=multi_db)
    n = dlg.auto_group("factor", ["PA100", "PB200"])

    assert n == 2                                  # Base · Hi
    assert st.manual_groups[("PA100", "01", None, None, None)] == \
        st.manual_groups[("PB200", "01", None, None, None)]   # lot을 넘어 묶인다
    assert ("PC300", "01", None, None, None) not in st.manual_groups

    # 멱등 — 다시 돌려도 같다
    before = dict(st.manual_groups)
    dlg.auto_group("factor", ["PA100", "PB200"])
    assert st.manual_groups == before


def test_auto_group_keeps_other_lots(qapp, multi_db, appdata):
    """★ 고르지 않은 lot의 배정·그룹은 살아남는다 — lot을 갈아 가며 작업한다."""
    st = AppState()
    dlg = _dialog(st, db_path=multi_db)
    dlg.auto_group("lot", ["PA100"])
    first = {g.gid: g.name for g in st.groups}

    dlg.auto_group("lot", ["PB200"])

    names = [g.name for g in st.groups]
    assert names == ["PA100", "PB200"]            # 앞의 것이 남아 있다
    assert ("PA100", "01", None, None, None) in st.manual_groups
    assert len({g.gid for g in st.groups}) == 2   # gid가 겹치지 않는다
    assert st.manual_groups[("PA100", "01", None, None, None)] in first


def test_auto_group_rerun_same_lots_is_idempotent(qapp, multi_db, appdata):
    st = AppState()
    dlg = _dialog(st, db_path=multi_db)
    dlg.auto_group("lot", ["PA100", "PB200"])
    before = dict(st.manual_groups)
    names = [g.name for g in st.groups]

    dlg.auto_group("lot", ["PA100", "PB200"])

    assert st.manual_groups == before
    assert [g.name for g in st.groups] == names   # 그룹이 불어나지 않는다


def test_single_lot_tab_still_rebuilds_everything(qapp, multi_db, appdata):
    """★ 단일 lot 탭(lots=None)은 지금까지처럼 처음부터 다시 만든다."""
    st = AppState()
    dlg = _dialog(st, db_path=multi_db)
    dlg.auto_group("lot", ["PA100"])

    dlg.auto_group("lot")                         # lots 없이 = 전체

    assert [g.name for g in st.groups] == ["PA100", "PB200", "PC300"]
    assert sum(1 for g in st.groups if g.ref) == 1


def test_factor_mode_merges_across_two_runs(qapp, multi_db, appdata):
    """★ factor는 gid가 조합마다 같으므로 lot을 나눠 돌려도 한 그룹으로 합쳐진다."""
    from etreport.model.split import SplitMatrix
    st = AppState()
    st.split = SplitMatrix.from_dataframe(pl.DataFrame([
        {"lot": "PA100", "wafer": "01", "M1": "Base"},
        {"lot": "PA100", "wafer": "02", "M1": "Hi"},
        {"lot": "PB200", "wafer": "01", "M1": "Base"},
        {"lot": "PB200", "wafer": "02", "M1": "Hi"},
    ]))
    st.factors = ["M1"]
    dlg = _dialog(st, db_path=multi_db)
    dlg.auto_group("factor", ["PA100"])
    dlg.auto_group("factor", ["PB200"])

    assert len(st.groups) == 2                    # Base · Hi — 늘지 않는다
    assert st.manual_groups[("PA100", "01", None, None, None)] == \
        st.manual_groups[("PB200", "01", None, None, None)]
    assert ("PA100", "02", None, None, None) in st.manual_groups


def test_factor_mode_is_a_noop_without_split(qapp, multi_db, appdata):
    st = AppState()
    dlg = _dialog(st, db_path=multi_db)

    assert dlg.auto_group("factor", ["PA100"]) == 0   # 예외를 던지지 않는다


def test_manual_edit_beats_factor_after_reload(qapp, multi_db, appdata):
    """★ 구워 둔 배정 위에 손으로 옮긴 것이 [적용] 후에도 이긴다."""
    from etreport.model.split import SplitMatrix
    st = AppState()
    st.split = SplitMatrix.from_dataframe(pl.DataFrame([
        {"lot": "PA100", "wafer": "01", "M1": "Base"},
        {"lot": "PA100", "wafer": "02", "M1": "Hi"},
    ]))
    st.factors = ["M1"]
    dlg = _dialog(st, db_path=multi_db)
    dlg.auto_group("factor", ["PA100"])
    other = next(g.gid for g in st.groups
                 if g.gid != st.manual_groups[("PA100", "01", None, None, None)])
    dlg._m_assign([("PA100", "01")], other)        # 한 장만 손으로 옮긴다

    loader.load_state(st, multi_db, lots=["PA100"])
    got = st.data.filter((pl.col("lot") == "PA100")
                         & (pl.col("wafer") == "01"))["gid"][0]
    assert got == other


def test_clearing_scope_also_clears_frame_gid(qapp, multi_db, appdata):
    """★ 배정을 지우면 프레임의 gid도 비운다 — 다시 배정받지 못한 wafer가
    예전 그룹에 속한 것처럼 남으면 안 된다."""
    st = AppState()
    loader.load_state(st, multi_db)
    dlg = _dialog(st, db_path=multi_db)
    dlg.auto_group("lot", ["PA100", "PB200", "PC300"])
    assert (st.data["gid"] != "").any()

    dlg._clear_scope(["PA100"])

    pa = st.data.filter(pl.col("lot") == "PA100")
    assert (pa["gid"] == "").all()                 # 지운 lot은 비었고
    assert (st.data.filter(pl.col("lot") == "PB200")["gid"] != "").all()


# ── 도크 lot 선택 규칙 ───────────────────────────────────────
def _workspace(db_path: str, appdata):
    from etreport.config.settings import Settings
    from etreport.model.state import StateBus
    from etreport.ui.analysis_ws import AnalysisWorkspace
    st, sett = AppState(), Settings.defaults()
    sett.analysis_configs[0].db_path = db_path
    return AnalysisWorkspace(st, StateBus(), sett), st, sett


def test_all_selected_means_empty_list(qapp, multi_db, appdata):
    """★ 전부 체크는 **빈 리스트**다 — SQL에 WHERE가 안 붙고, 나중에 적재로
    lot이 늘어도 그 lot이 조용히 빠지지 않는다."""
    from PySide6.QtCore import Qt
    ws, st, sett = _workspace(multi_db, appdata)

    assert st.lots_all == ["PA100", "PB200", "PC300"]
    assert st.lots_selected == []                  # 전부 = 빈 리스트
    assert "3/3" in ws.lot_section.toggle.text()
    assert sett.lot_selections.get(multi_db) is None

    ws.lot_list.item(1).setCheckState(Qt.Unchecked)
    assert st.lots_selected == ["PA100", "PC300"]
    assert sett.lot_selections[multi_db] == ["PA100", "PC300"]
    assert "2/3" in ws.lot_section.toggle.text()

    ws.lot_list.item(1).setCheckState(Qt.Checked)   # 되돌리면 기록도 지운다
    assert st.lots_selected == []
    assert sett.lot_selections.get(multi_db) is None


def test_saved_selection_is_restored_and_pruned(qapp, multi_db, appdata):
    """★ 기억해 둔 선택은 되살아나되, 지금 DB에 없는 lot은 버린다."""
    from etreport.config.settings import Settings
    from etreport.model.state import StateBus
    from etreport.ui.analysis_ws import AnalysisWorkspace
    st, sett = AppState(), Settings.defaults()
    sett.analysis_configs[0].db_path = multi_db
    sett.lot_selections[multi_db] = ["PA100", "없어진lot"]
    AnalysisWorkspace(st, StateBus(), sett)

    assert st.lots_selected == ["PA100"]


# ── 예전 DB 호환 ─────────────────────────────────────────────
def test_lot_filter_on_hand_made_db(tmp_path):
    """★ 손으로 만든 wide 테이블(예전 DB)에서도 lot 필터가 걸린다."""
    p = tmp_path / "old.duckdb"
    con = duckdb.connect(str(p))
    con.execute("CREATE TABLE et_data AS SELECT * FROM (VALUES "
                "('PA','01',0.4), ('PB','01',0.5)) AS t(lot, wafer, Vt)")
    con.close()

    st = AppState()
    loader.load_state(st, str(p), lots=["PA"])
    assert st.data["lot"].to_list() == ["PA"]
    loader.close_store(st)
