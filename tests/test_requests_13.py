"""요청 13건(2026-08-18~19) 회귀.

  1  단일 exe 빌드 정의(.spec)와 리소스 경로
  2  fab tracking — 뽑을 컬럼의 **이름을 사용자가 정한다**
  3  조회 조건(line·process·part·기간)을 분석 DB에서 자동으로 채운다
  4  exe에서 style.qss를 찾는다
  5  실행 중인 빌드가 언제·어느 소스로 만들어졌는지 확인할 수 있다
  6  boxplot — x축은 lot+wafer·그룹·측정 조건·tracking 컬럼
  7  plot 종류를 화면에서 고른다
  8  VARCHAR 타입 때문에 읽기가 막히지 않는다
  9  fab tracking·계측 결과가 축과 PPT로 이어진다
 10  업데이트 체크·적용이 단일 exe를 다룬다
 12  Tukey(IQR) 이상치 필터 — 배수는 사용자가 정하고 기록이 남는다
 13  추출 예약 실행 — 앱이 꺼져 있어도 돈다
"""
from __future__ import annotations

import os

import polars as pl
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ── 1·4·5  빌드·리소스 ──────────────────────────────────────
def test_spec_exists_and_leaves_room_for_site_extras():
    """사내에서 인증서·라이브러리를 더 실을 자리가 spec에 있어야 한다(요청 11)."""
    from pathlib import Path

    spec = Path(__file__).resolve().parents[1] / "build" / "ETReport.spec"
    text = spec.read_text(encoding="utf-8")
    for token in ("SITE_DATAS", "SITE_BINARIES", "SITE_HIDDEN",
                  "site_extras.py"):
        assert token in text, f"{token}을(를) 고칠 자리가 없다"
    # 소스 트리가 site-packages보다 먼저여야 "고쳐도 exe가 그대로"가 안 생긴다
    assert "pathex=[str(SRC)]" in text
    assert "console=False" in text          # --windowed


def test_qss_is_found_through_resources(monkeypatch, tmp_path):
    """style.qss는 모듈 옆이 아니라 **번들에서** 찾아야 한다(요청 4).

    PyInstaller는 모듈을 압축 아카이브에 넣으므로 `__file__` 옆에는 아무것도
    없다. `sys._MEIPASS`를 흉내 내서 그쪽을 보는지 확인한다.
    """
    import sys

    from etreport import resources
    from etreport.ui import theme

    bundle = tmp_path / "_MEI"
    (bundle / "etreport" / "ui").mkdir(parents=True)
    (bundle / "etreport" / "ui" / "style.qss").write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    resources.clear_cache()
    try:
        assert theme.qss_path() == bundle / "etreport" / "ui" / "style.qss"
        assert theme.qss_path().exists()
    finally:
        resources.clear_cache()


def test_qss_falls_back_to_source_tree():
    """소스 실행에서는 지금까지처럼 패키지 안의 파일을 쓴다."""
    from etreport import resources
    from etreport.ui import theme

    resources.clear_cache()
    assert theme.qss_path().exists()
    assert theme.qss_path().name == "style.qss"


def test_build_info_labels_the_running_build():
    """'고쳤는데 exe가 그대로'를 가를 근거(요청 5)."""
    from etreport import buildinfo

    b = buildinfo.get()
    assert b.version
    assert "v" in b.label()
    # 소스 실행이면 스탬프가 없고, 그 사실이 라벨에 드러나야 한다
    assert b.mode in ("source", "onefile", "onedir")


# ── 2·3  fab tracking ───────────────────────────────────────
@pytest.fixture
def tracking():
    from etreport import demo_data
    return demo_data.tracking_frame()


def test_named_columns_replace_the_process_id_header(tracking):
    """예전에는 process_id 값이 그대로 머리글이었다 — 이제 이름을 정한다(요청 2)."""
    from etreport.data import fabtracking as ft

    steps = ft.split_steps(tracking)
    cols = [
        ft.TrackColumn(name="PHOTO_recipe", source=ft.AUTO_SOURCE, step=steps[0]),
        ft.TrackColumn(name="ETCH_설비", source="eqp_id", step=steps[0]),
        ft.TrackColumn(name="seq", source="step_seq"),
    ]
    got = ft.derive(tracking, cols)
    assert got.columns == ["lot", "wafer", "PHOTO_recipe", "ETCH_설비", "seq"]
    assert got.height > 0
    # 같은 step에서 서로 다른 원본을 **두 열로** 뽑을 수 있어야 한다
    assert got["PHOTO_recipe"].to_list() != got["ETCH_설비"].to_list()


def test_suggested_columns_keep_the_old_result(tracking):
    """기본 제안은 지금까지와 같은 결과 — 놀라게 하지 않는다."""
    from etreport.data import fabtracking as ft

    cols = ft.suggest_columns(tracking)
    names = [c.name for c in cols]
    assert names == ft.split_steps(tracking)
    assert all(c.source == ft.AUTO_SOURCE for c in cols)


def test_safe_name_keeps_every_column():
    """이름이 이상해도 컬럼을 버리지 않는다 — 다듬고 중복은 번호를 붙인다."""
    from etreport.data import fabtracking as ft

    taken: set[str] = set()
    out = []
    for raw in ("PHOTO recipe", "PHOTO recipe", "", '나쁜"이름'):
        n = ft.safe_name(raw, taken)
        taken.add(n)
        out.append(n)
    assert len(set(out)) == 4                 # 하나도 겹치지 않는다
    assert all(" " not in n and '"' not in n for n in out)


def test_attach_normalises_wafer_notation(tracking):
    """tracking은 `01`, ET DB는 `W01` — 그대로 조인하면 전부 null이 된다."""
    from etreport.data import fabtracking as ft

    values = pl.DataFrame({"lot": ["PA123"], "wafer": ["1"], "RECIPE": ["A"]})
    data = pl.DataFrame({"key": ["k1"], "lot": ["PA123"], "wafer": ["W01"],
                         "gid": [""], "step": [None], "temp": [None],
                         "site": [None]})
    got, names = ft.attach(data, values)
    assert names == ["RECIPE"]
    assert got["RECIPE"].to_list() == ["A"]


def test_attach_refreshes_instead_of_duplicating():
    """조건을 고쳐 다시 뽑으면 **덮어쓴다** — 안 그러면 고친 결과가 안 보인다."""
    from etreport.data import fabtracking as ft

    data = pl.DataFrame({"key": ["k1"], "lot": ["PA123"], "wafer": ["W01"],
                         "gid": [""], "RECIPE": ["옛값"]})
    got, names = ft.attach(
        data, pl.DataFrame({"lot": ["PA123"], "wafer": ["W01"],
                            "RECIPE": ["새값"]}))
    assert names == ["RECIPE"]
    assert got["RECIPE"].to_list() == ["새값"]
    assert got.columns.count("RECIPE") == 1


def test_tracking_sql_takes_process_and_part_and_dates():
    """조회 조건을 SQL에 실을 수 있어야 한다(요청 3)."""
    from datetime import date

    from etreport.data import fabtracking as ft

    sql = ft.build_tracking_sql(
        lots=["PA123"], line_id="KFBK", process_ids=["8NM"], part_ids=["SRAM"],
        d_from=date(2026, 2, 1), d_to=date(2026, 8, 1))
    assert "process_id IN ('8NM')" in sql
    assert "part_id IN ('SRAM')" in sql
    assert "tkout_time >= '2026-02-01 00:00:00'" in sql
    assert "tkout_time <  '2026-08-02 00:00:00'" in sql       # 끝일 포함


def test_tracking_sql_without_new_conditions_is_unchanged():
    """비우면 예전과 같은 SQL — 지금까지 돌던 조회가 조용히 바뀌면 안 된다."""
    from etreport.data import fabtracking as ft

    assert (ft.build_tracking_sql(lots=["PA123"])
            == ft.build_tracking_sql(lots=["PA123"], process_ids=None,
                                     part_ids=[]))


def test_lot_context_reads_conditions_and_defaults_to_180_days(tmp_path):
    """분석 DB에서 line·process·part·기간을 읽어 온다(요청 3)."""
    from datetime import date, timedelta

    import duckdb

    from etreport.data import lotcontext

    db = tmp_path / "et.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE et_data AS SELECT * FROM (VALUES
          ('KFBK','8NM','SRAM','PA123','W01', TIMESTAMP '2026-08-05 10:00'),
          ('KFBK','8NM','SRAM','PA123','W02', TIMESTAMP '2026-08-07 10:00'))
        t(line_id, process_id, part_id, root_lot_id, wafer_id, tkout_time)
    """)
    con.close()

    ctx = lotcontext.from_db(str(db), ["PA123"])
    assert ctx.line_ids == ["KFBK"]
    assert ctx.process_ids == ["8NM"]
    assert ctx.part_ids == ["SRAM"]
    lo, hi = ctx.date_range()
    assert hi == date(2026, 8, 7)
    assert lo == date(2026, 8, 5) - timedelta(days=180)


def test_lot_context_is_empty_when_the_db_has_no_such_columns(tmp_path):
    """없는 스키마에서도 조용히 빈 값 — 창은 떠야 한다."""
    import duckdb

    from etreport.data import lotcontext

    db = tmp_path / "bare.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE et_data AS SELECT 1 AS x")
    con.close()
    ctx = lotcontext.from_db(str(db))
    assert ctx.line_ids == [] and ctx.date_range() == (None, None)


# ── 8  타입 때문에 읽기가 막히지 않는다 ─────────────────────
def _varchar_db(tmp_path, name="old.duckdb"):
    import duckdb

    db = tmp_path / name
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE et_data AS SELECT * FROM (VALUES
          ('PA1','W01',1,1,'25.0','M1',1,9,'2026-08-01 10:00','1.5','0.02'),
          ('PA1','W01',1,1,'23.9','M1',2,9,'2026-08-01 10:01','1.6','0.03'),
          ('PA1','W02',2,1,'n/a', 'M1',1,9,'2026-08-01 10:02','1.7','0.04'),
          ('PA1','W02',2,1,'',    'M1',1,9,'2026-08-01 10:03','bad','0.05'))
        t(root_lot_id, wafer_id, chip_x_pos, chip_y_pos, temperature,
          step_id, step_seq, total_site_cnt, tkout_time, VT_N, IOFF_N)
    """)
    con.close()
    return db


def test_varchar_temperature_does_not_block_the_read(tmp_path):
    """예전 DB는 temperature가 VARCHAR다 — 그것 때문에 로딩이 막혔다(요청 8)."""
    import duckdb

    from etreport.data import compat

    db = _varchar_db(tmp_path)
    con = duckdb.connect(str(db), read_only=True)
    try:
        prof = compat.profile(con, "et_data")
        got = con.execute(compat.select_sql(prof)).pl()
    finally:
        con.close()
    assert got.height > 0
    assert got.schema["temp"].is_numeric()
    assert 25 in got["temp"].to_list()          # '25.0'·'23.9' 모두 25로


def test_unreadable_temperature_becomes_null_not_an_error(tmp_path):
    """`'n/a'` 한 칸 때문에 조회 전체가 떨어지면 안 된다."""
    import duckdb

    from etreport.data import compat

    db = _varchar_db(tmp_path, "bad.duckdb")
    con = duckdb.connect(str(db), read_only=True)
    try:
        prof = compat.profile(con, "et_data")
        got = con.execute(compat.select_sql(prof)).pl()
    finally:
        con.close()
    assert None in got["temp"].to_list()


def test_varchar_values_are_read_as_numbers(tmp_path):
    """숫자 item이 하나도 없으면 문자열을 숫자로 읽는다 — 표·plot을 그리려면."""
    import duckdb

    from etreport.data import compat

    db = _varchar_db(tmp_path, "vals.duckdb")
    con = duckdb.connect(str(db), read_only=True)
    try:
        prof = compat.profile(con, "et_data")
        assert set(prof.items) == {"VT_N", "IOFF_N"}
        got = con.execute(compat.select_sql(prof)).pl()
    finally:
        con.close()
    assert got.schema["VT_N"].is_numeric()
    assert 1.5 in got["VT_N"].to_list()


def test_numeric_db_sql_is_unchanged():
    """숫자 컬럼만 있는 DB의 SQL은 예전과 같아야 한다(TRY_CAST가 끼지 않는다)."""
    from etreport.data import compat

    prof = compat.TableProfile(
        table="et_data",
        columns={"root_lot_id": "VARCHAR", "wafer_id": "VARCHAR",
                 "VT_N": "DOUBLE"},
        roles={"lot": "root_lot_id", "wafer": "wafer_id"},
        items=["VT_N"])
    assert "TRY_CAST" not in compat.select_sql(prof)


# ── 6·7  boxplot · plot 종류 ────────────────────────────────
@pytest.fixture
def demo_state():
    from etreport import demo
    from etreport.model.state import AppState

    st = AppState()
    demo.load_demo(st)
    return st


def test_category_choices_include_lot_wafer_and_tracking(demo_state):
    """boxplot x축 후보(요청 6·9)."""
    from etreport.model import categories as cat

    st = demo_state
    st.data = st.data.with_columns(pl.lit("A").alias("PHOTO_recipe"))
    got = cat.choices(st.data, ["PHOTO_recipe"])
    assert got[0] == cat.LOT_WAFER
    for name in ("lot", "wafer", cat.GROUP, "temp", "PHOTO_recipe"):
        assert name in got
    # 숫자 item은 후보가 아니다 — 값마다 상자가 하나씩 생긴다
    assert "Idsat N SVT" not in got


def test_lot_wafer_is_a_virtual_column(demo_state):
    """DB에 없는 열이지만 x축으로 쓸 수 있어야 한다."""
    from etreport.model import categories as cat

    s = cat.series(demo_state.data, cat.LOT_WAFER)
    assert cat.SEP in s[0]
    assert s.len() == demo_state.data.height


def test_group_axis_shows_names_not_gids(demo_state):
    """gid는 `x0` 같은 내부 값이라 축에 그대로 적으면 뜻이 없다."""
    from etreport.model import categories as cat

    labels = cat.group_labels(demo_state.groups)
    s = cat.series(demo_state.data, cat.GROUP, labels)
    assert set(s.to_list()) & set(labels.values())


def test_category_order_is_numeric_when_it_can_be():
    """온도 `-40 25 125`가 사전 순으로 늘어서면 축을 읽을 수 없다."""
    from etreport.model import categories as cat

    assert cat.order(["125", "25", "-40"]) == ["-40", "25", "125"]
    assert cat.order(["b", "a"]) == ["a", "b"]


def test_boxplot_renders_one_box_per_category(demo_state):
    from etreport.model.specs import PlotSpec
    from etreport.render import mpl_renderer as R

    st = demo_state
    styles = st.groups
    data = {g.gid: st.data.filter(pl.col("gid") == g.gid) for g in styles}
    item = next(c for c in st.data.columns if c.startswith("Idsat"))
    spec = PlotSpec(title="t", x="temp", y=item, type="box", mode="site")
    fig = R.render(spec, data, styles, st.rf, st.log_patterns, (6, 4))
    ax = fig.axes[0]
    ticks = [t.get_text() for t in ax.get_xticklabels()]
    assert ticks and len(ticks) == len({str(t) for t in st.data["temp"]})
    assert ax.patches                       # 상자가 실제로 그려졌다


def test_boxplot_survives_an_unknown_category(demo_state):
    """x에 없는 이름을 적어도 죽지 않고 '그릴 값이 없습니다'로 끝난다."""
    from etreport.model.specs import PlotSpec
    from etreport.render import mpl_renderer as R

    st = demo_state
    spec = PlotSpec(title="t", x="없는컬럼", y=st.aliases()[0], type="box")
    fig = R.render(spec, {"": st.data}, st.groups[:1], st.rf,
                   st.log_patterns, (6, 4))
    assert fig.axes                          # 예외 없이 Figure가 나온다


def test_plot_types_are_shared_by_template_and_ui():
    """화면 콤보와 템플릿 Type 열이 같은 목록을 봐야 한다(요청 7)."""
    from etreport.model.specs import PLOT_TYPE_LABELS, PLOT_TYPES

    assert PLOT_TYPES == ("scatter", "box", "trend")
    assert set(PLOT_TYPE_LABELS) == set(PLOT_TYPES)


def test_template_accepts_box_rows(rf_small):
    """Type=box 행은 x를 ALIAS로 검사하지 않는다(범주 이름이기 때문에)."""
    from etreport.model import templates as T

    alias = rf_small.rules[0].alias
    plot = pl.DataFrame({
        "page": [1, 1], "x": ["lot+wafer", ""], "y": [alias, alias],
        "order": [1, 2], "title1": ["p", "p"], "title2": ["a", "b"],
        "Report": ["R", "R"], "Type": ["box", "box"],
        "x_name": ["", ""], "y_name": ["", ""]})
    table = pl.DataFrame({"item_id": [alias], "CAT1": ["c"], "Report": ["R"]})
    t = T.from_frames(plot, table, rf_small)
    spec = T.build_report(t, "R")
    slots = [s for s in spec.pages[0].slots if s]
    assert len(slots) == 1                      # x가 빈 두 번째 행은 건너뛴다
    assert slots[0].type == "box" and slots[0].x == "lot+wafer"


# ── 12  Tukey 이상치 필터 ───────────────────────────────────
def test_tukey_filters_more_as_k_gets_smaller(demo_state):
    from etreport.data.loader import item_columns
    from etreport.model import outliers as ol

    items = item_columns(demo_state.data)
    counts = [len(ol.find(demo_state.data, items,
                          ol.TukeyConfig(True, k, ol.SCOPE_COND)).points)
              for k in (1.5, 3.0, 4.5)]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > 0


def test_tukey_records_the_reason_like_an_exclusion(demo_state):
    """'제외하기처럼 따로 기록' — 무엇이 왜 빠졌는지 적혀 있어야 한다."""
    from etreport.data.loader import item_columns
    from etreport.model import outliers as ol

    res = ol.find(demo_state.data, item_columns(demo_state.data),
                  ol.TukeyConfig(True, 3.0, ol.SCOPE_COND))
    assert res.points
    rec = next(iter(res.points.values()))
    assert "3" in rec["reason"] and "IQR" in rec["reason"]
    assert rec["item"] in demo_state.data.columns
    assert rec["at"]


def test_tukey_does_not_touch_small_groups():
    """점 몇 개짜리 묶음에서 IQR을 구하면 멀쩡한 값이 이상치가 된다."""
    from etreport.model import outliers as ol

    df = pl.DataFrame({"key": [f"k{i}" for i in range(5)],
                       "step": ["M1"] * 5, "temp": [25] * 5,
                       "V": [1.0, 1.0, 1.0, 1.0, 99.0]})
    res = ol.find(df, ["V"], ol.TukeyConfig(True, 3.0, ol.SCOPE_COND))
    assert not res.points


def test_tukey_keeps_measurement_conditions_apart():
    """25 ℃와 150 ℃를 섞으면 정상적인 고온 측정이 통째로 이상치가 된다."""
    from etreport.model import outliers as ol

    lo = [1.0 + i * 0.01 for i in range(20)]
    hi = [5.0 + i * 0.01 for i in range(20)]
    df = pl.DataFrame({
        "key": [f"k{i}" for i in range(40)],
        "step": ["M1"] * 40,
        "temp": [25] * 20 + [150] * 20,
        "V": lo + hi})
    by_cond = ol.find(df, ["V"], ol.TukeyConfig(True, 1.5, ol.SCOPE_COND))
    by_all = ol.find(df, ["V"], ol.TukeyConfig(True, 1.5, ol.SCOPE_ALL))
    assert not by_cond.points          # 조건별로 보면 각자 촘촘하다
    assert not by_all.points           # (합쳐도 IQR이 커져 여기서는 안 걸린다)
    # 조건별 IQR이 훨씬 좁다는 것이 요점 — 한쪽에만 튀는 값을 넣어 확인한다
    df2 = df.with_columns(
        pl.when(pl.col("key") == "k0").then(2.0).otherwise(pl.col("V")).alias("V"))
    assert ol.find(df2, ["V"], ol.TukeyConfig(True, 1.5, ol.SCOPE_COND)).points
    assert not ol.find(df2, ["V"], ol.TukeyConfig(True, 1.5, ol.SCOPE_ALL)).points


def test_hidden_merges_exclusions_and_filter(demo_state):
    """읽는 쪽은 전부 hidden()을 봐야 화면과 PPT가 갈리지 않는다."""
    from etreport.model import outliers as ol

    st = demo_state
    before = st.active().height
    ol.apply(st, ol.TukeyConfig(True, 3.0, ol.SCOPE_COND))
    assert st.filtered
    assert st.hidden() == st.excluded | set(st.filtered)
    assert st.active().height < before


def test_turning_the_filter_off_restores_every_point(demo_state):
    """껐는데 걸러진 점이 남아 있으면 왜 없는지 알 방법이 없다."""
    from etreport.model import outliers as ol

    st = demo_state
    ol.apply(st, ol.TukeyConfig(True, 3.0))
    ol.apply(st, ol.TukeyConfig(False, 3.0))
    assert st.filtered == {}
    assert st.active().height == st.data.height - len(st.excluded)


def test_filtered_points_are_kept_in_the_ppt_history(demo_state):
    """덱을 받은 사람이 '무엇이 빠졌나'를 알 수 있어야 한다."""
    from etreport.data import loader
    from etreport.model import outliers as ol

    st = demo_state
    ol.apply(st, ol.TukeyConfig(True, 3.0))
    manual = loader.exclusion_frame(st)
    both = loader.exclusion_frame(st, include_filtered=True)
    assert both.height == manual.height + len(st.filtered)
    assert any("IQR" in r for r in both["reason"].to_list())


def test_filter_sidecar_is_a_separate_file(tmp_path, appdata):
    """손으로 찍은 제외와 섞으면 필터를 끌 때 사람이 뺀 점까지 지워야 한다."""
    from etreport.data import exclusions

    db = str(tmp_path / "x.duckdb")
    exclusions.save(db, {"a": {"reason": "손", "at": "t"}})
    exclusions.save_filtered(db, {"b": {"reason": "IQR", "at": "t"}})
    assert list(exclusions.load(db)) == ["a"]
    assert list(exclusions.load_filtered(db)) == ["b"]


# ── 10  업데이트 ────────────────────────────────────────────
def test_update_prefers_a_single_exe_asset():
    from etreport.update import checker

    assets = [{"name": "ETReport-1.4.0-win64.zip"},
              {"name": "ETReport-1.4.0-win64.exe"}]
    assert checker.pick_asset(assets)["name"].endswith(".exe")
    assert checker.pick_asset(assets[:1])["name"].endswith(".zip")
    assert checker.pick_asset([{"name": "notes.txt"}]) is None


def test_update_plan_picks_the_right_replacement(tmp_path):
    from etreport.update import apply as upd

    exe = tmp_path / "ETReport.exe"
    exe.write_bytes(b"x")
    kind, src = upd.plan(exe)
    assert kind == "exe" and src == exe


# ── 13  예약 실행 ───────────────────────────────────────────
def test_schedule_command_line_quotes_paths_with_spaces(monkeypatch):
    import sys

    from etreport import schedule as sched

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\ET\ETReport.exe")
    cmd = sched.command_line(sched.Job(preset="야간 추출", days=3))
    assert '"C:\\Program Files\\ET\\ETReport.exe"' in cmd
    assert '--run-extract "야간 추출"' in cmd
    assert "--days 3" in cmd


def test_schedule_refuses_outside_windows(monkeypatch):
    """되는 것과 안 되는 것을 숨기지 않는다."""
    import sys

    from etreport import schedule as sched

    monkeypatch.setattr(sys, "platform", "linux")
    ok, why = sched.available()
    assert not ok and "Windows" in why
    with pytest.raises(sched.ScheduleUnavailable):
        sched.register(sched.Job(preset="x"))


def test_headless_run_reports_failure_with_a_nonzero_code(appdata):
    """실패를 0으로 돌려주면 비어 있는 DB를 아무도 모르게 된다."""
    from etreport.config.settings import Settings
    from etreport.schedule import run_headless

    Settings.defaults().save()
    assert run_headless("없는 프리셋", days=1) == 2


def test_pipeline_is_reachable_without_qt():
    """파이프라인이 화면 안에 있으면 예약이 같은 코드를 한 벌 더 쓰게 된다."""
    import inspect

    from etreport.data import pipeline

    src = inspect.getsource(pipeline)
    # 설명(docstring)에는 QThread 이야기가 나오지만 **코드에는 없어야** 한다
    assert "import PySide6" not in src
    assert "from PySide6" not in src
    assert "_rf.parquet" in src            # 원본 보존 규칙도 여기로 옮겨왔다


# ── 창 조립 스모크 ──────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def test_fabtrack_dialog_builds_and_writes_sql(qapp, demo_state, appdata):
    """조건 → SQL이 창에서 만들어져야 한다(요청 2·3)."""
    from etreport.ui.widgets.fabtrack_dialog import FabTrackDialog

    dlg = FabTrackDialog(demo_state, lots=["PA123"])
    try:
        sql = dlg.ed_sql.toPlainText()
        assert "f_fab_tracking" in sql and "PA123" in sql
        assert dlg.build_sql() == sql            # [다시 만들기]와 같은 결과
        # 컬럼 표에 줄을 더하면 이름이 정리돼 나온다
        dlg._add_row("PHOTO recipe", "reticle_id", "")
        got = dlg.spec_columns()
        assert got[-1].name == "PHOTO_recipe"
        assert got[-1].source == "reticle_id"
    finally:
        dlg.deleteLater()


def test_metrology_dialog_defaults_to_a_dated_query(qapp, demo_state, appdata):
    """계측 조회에도 기간과 SQL 편집이 있어야 한다(요청 3)."""
    from etreport.ui.widgets.metrology_dialog import MetrologyDialog

    dlg = MetrologyDialog(demo_state)
    try:
        sql = dlg.ed_sql.toPlainText()
        assert "f_fab_wf_met" in sql
        assert "root_lot_id IN" in sql            # lot으로 반드시 좁힌다
    finally:
        dlg.deleteLater()


def test_explore_tab_has_a_plot_type_combo(qapp, demo_state):
    """종류를 고르면 X도 그 종류가 읽을 수 있는 값이 된다(요청 7)."""
    from etreport.model import categories as cat
    from etreport.model.state import StateBus
    from etreport.ui.tabs.explore import ExploreTab

    tab = ExploreTab(demo_state, StateBus())
    try:
        assert tab.cmb_type.count() == 3
        tab.cmb_type.setCurrentIndex(1)           # boxplot
        tab._type_changed()
        assert demo_state.explore.type == "box"
        assert demo_state.explore.x == cat.LOT_WAFER
        assert cat.LOT_WAFER in tab._x_items()
        tab.cmb_type.setCurrentIndex(0)           # 산점도로 되돌리면 X도 item
        tab._type_changed()
        assert demo_state.explore.type == "scatter"
        assert demo_state.explore.x in tab._y_items()
    finally:
        tab.deleteLater()


def test_schedule_dialog_builds(qapp, appdata):
    from etreport.config.settings import ExtractPreset
    from etreport.ui.widgets.schedule_dialog import ScheduleDialog

    p = ExtractPreset(name="야간", db_path="a.duckdb", reformatter_path="r.xlsx")
    dlg = ScheduleDialog(p)
    try:
        assert "--run-extract" in dlg.cmd.toPlainText()
        assert dlg.job().preset == "야간"
    finally:
        dlg.deleteLater()
