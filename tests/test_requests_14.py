"""요청 14건 회귀 — 계측 재적용·fab tracking 표·조건 연산자·GEN·적재 보전·
공통 legend·lot 검색·parquet 재사용·업데이트 교체·summary SPEC 열.

각 테스트의 제목 끝 `(§N)`이 요청 번호다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date
from typing import ClassVar

import pytest

from etreport.config.catalog import Catalog
from etreport.config.settings import Condition
from etreport.data import querybuilder as qb
from etreport.update import apply as upd_apply
from etreport.update import checker
from tests import test_ux_redesign as _ux

# 창 조립 픽스처는 한 벌만 둔다 — 같은 것을 다시 쓰면 데모 로딩이 두 번 돈다.
qapp = _ux.qapp
win = _ux.win


def _cat() -> Catalog:
    c = Catalog()
    c.columns = []
    return c


# ── §5 data_class ─────────────────────────────────────────────────
def test_where_pins_data_class():
    """일반 측정만 본다 — 추출·미리보기·probe가 모두 같은 조건을 건다. (§5)"""
    conds = [Condition("line_id", "L1", required=True)]
    cat, d = _cat(), date(2026, 8, 4)
    for sql in (qb.build_extract_sql(conds, d, d, cat, ["A"]),
                qb.build_preview_sql(conds, d, d, cat, 3),
                qb.build_item_probe_sql(conds, d, d, cat, 10)):
        assert "data_class = 'GEN'" in sql


# ── §12 업데이트 교체 ──────────────────────────────────────────────
def test_update_batch_waits_without_a_console():
    """`timeout`은 콘솔 없는 배치에서 즉시 실패한다 — 기다리는 척만 했다. (§12)"""
    for bat in (upd_apply._BAT_DIR, upd_apply._BAT_EXE):
        assert "timeout /t" not in bat
        assert "ping -n" in bat                  # 콘솔 없이도 진짜로 쉰다
        assert "goto copy" in bat                # 잠금이 풀릴 때까지 재시도
        bat.encode("ascii")


def test_staged_exe_is_left_beside_the_old_one(tmp_path):
    """교체가 실패해도 손으로 이름만 바꾸면 되도록 exe 옆에 남긴다. (§12)"""
    src = tmp_path / "dl" / "ETReport-9.9.9-win64.exe"
    src.parent.mkdir()
    src.write_bytes(b"new")
    cur = tmp_path / "app" / "ETReport.exe"
    cur.parent.mkdir()
    cur.write_bytes(b"old")

    staged = upd_apply.stage_beside(src, cur)
    assert staged == cur.with_name("ETReport_temp.exe")
    assert staged.read_bytes() == b"new"
    assert cur.read_bytes() == b"old"            # 앱이 도는 동안 현재 exe는 그대로


def test_staging_reports_unwritable_install_dir(tmp_path):
    """권한이 없으면 앱이 살아 있을 때 알린다 — 닫은 뒤 조용히 실패하지 않게. (§12)"""
    src = tmp_path / "new.exe"
    src.write_bytes(b"new")
    cur = tmp_path / "없는폴더" / "ETReport.exe"
    with pytest.raises(upd_apply.UpdateNotApplicable):
        upd_apply.stage_beside(src, cur)


# ── §13 사내 GHE ──────────────────────────────────────────────────
def test_update_checker_points_at_the_internal_repo():
    """사내 GHE·저장소·사설 CA 설정. (§13)"""
    assert checker.API_BASE == "https://github.samsungds.net/api/v3"
    assert (checker.OWNER, checker.REPO) == ("jiho7879-kim", "PA3_SRAM")
    assert checker.TOKEN and checker.CA_BUNDLE is False
    assert "Bearer" in checker._headers()["Authorization"]


# ── §11 추출 원본 재사용 ───────────────────────────────────────────
def test_chunk_path_is_decided_by_the_sql(tmp_path):
    """같은 조회 = 같은 파일 이름. uuid면 재사용할 길이 없었다. (§11)"""
    from etreport.data.extractor import Chunk, Unit, chunk_path

    u = Unit(Chunk(date(2026, 8, 4), date(2026, 8, 4)), 0, 1, ["A"])
    a = chunk_path(tmp_path, u, "SELECT 1")
    assert a == chunk_path(tmp_path, u, "SELECT 1")
    assert a != chunk_path(tmp_path, u, "SELECT 2")
    assert a.name.startswith("raw_20260804_20260804_")


def test_reuse_skips_the_query(tmp_path, monkeypatch):
    """받아 둔 parquet이 있으면 bdq를 부르지 않는다. 꺼져 있으면 부른다. (§11)"""
    import polars as pl

    from etreport.data import extractor as ex

    calls = []

    def fake_fetch(sql):
        calls.append(sql)
        return pl.DataFrame({"line_id": ["L1"], "root_lot_id": ["PA1"],
                             "wafer_id": ["01"], "chip_x_pos": [1],
                             "chip_y_pos": [1], "temperature": [25.0],
                             "step_id": ["M1"], "step_seq": [1],
                             "total_site_cnt": [9],
                             "tkout_time": ["2026-08-04 01:00:00"],
                             "item_id": ["Vt"], "et_value": [0.5]})

    monkeypatch.setattr(ex, "_fetch", fake_fetch)
    conds = [Condition("line_id", "L1", required=True)]
    d = date(2026, 8, 4)
    args = (conds, d, d, _cat(), tmp_path)

    first = ex.extract_to_parquet(*args, item_ids=["Vt"], reuse=True)
    assert len(calls) == 1 and len(first) == 1
    again = ex.extract_to_parquet(*args, item_ids=["Vt"], reuse=True)
    assert len(calls) == 1 and again == first          # 재사용 — 조회 없음
    ex.extract_to_parquet(*args, item_ids=["Vt"], reuse=False)
    assert len(calls) == 2                             # 꺼 두면 늘 다시 받는다


def test_stale_staging_is_not_reused(tmp_path):
    """보관 기간이 지난 파일은 재사용하지 않는다 — cleanup과 같은 기준. (§11)"""
    import os
    import time as _t

    from etreport.data.extractor import is_fresh

    f = tmp_path / "raw.parquet"
    f.write_bytes(b"x")
    assert is_fresh(f)
    os.utime(f, (0, _t.time() - 8 * 86400))
    assert not is_fresh(f)
    assert not is_fresh(tmp_path / "없음.parquet")


# ── §9 lot 검색 ───────────────────────────────────────────────────
def test_lot_search_hides_without_unchecking(qapp, win):
    """검색은 **숨기기만** 한다 — 안 보이는 lot이 조용히 빠지면 안 된다. (§9)"""
    from PySide6.QtCore import Qt

    ws = win.anal_ws
    ws.state.lots_all = ["PA1", "PA2", "PB9"]
    ws.state.lots_selected = []
    ws._fill_lot_list()

    ws.lot_search.setText("pa")
    vis = [ws.lot_list.item(i) for i in range(ws.lot_list.count())
           if not ws.lot_list.item(i).isHidden()]
    assert [x.data(Qt.UserRole) for x in vis] == ["PA1", "PA2"]
    # 숨겼다고 체크가 풀리지 않는다 = SQL이 좁아지지 않는다
    ws._collect_lots()
    assert ws.state.lots_selected == []

    # [해제]는 보이는 것에만 — 찾아 놓고 고르는 흐름
    ws._lot_check_all(False)
    ws._collect_lots()
    assert ws.state.lots_selected == ["PB9"]

    ws.lot_search.setText("")
    assert not any(ws.lot_list.item(i).isHidden()
                   for i in range(ws.lot_list.count()))


# ── §6 재적재 — 빈 item 채우기 ─────────────────────────────────────
def _long(items: dict[str, float], seq: int = 1):
    """long 한 chip 분량. `items`는 item_id → 값."""
    import polars as pl

    n = len(items)
    return pl.DataFrame({
        "line_id": ["L1"] * n, "root_lot_id": ["PA1"] * n,
        "wafer_id": ["01"] * n, "chip_x_pos": [1] * n, "chip_y_pos": [1] * n,
        "temperature": [25.0] * n, "step_id": ["M1"] * n, "step_seq": [seq] * n,
        "total_site_cnt": [9] * n,
        "tkout_time": [__import__("datetime").datetime(2026, 8, 4, 1)] * n,
        "item_id": list(items), "value": list(items.values())})


def _reload(dbp, frame, tmp_path, name):
    from etreport.data.db import Store, pivot_and_load

    p = tmp_path / name
    frame.write_parquet(p)
    store = Store(dbp)
    try:
        return pivot_and_load(store, [p]), store.filled
    finally:
        store.close()


def test_reload_fills_the_new_item_instead_of_dropping_the_row(tmp_path):
    """item을 더해 같은 기간을 다시 적재하면 **그 칸이 채워진다**. (§6)

    예전에는 key_hash가 같다고 ANTI JOIN이 행을 통째로 버려서, 새 item 컬럼이
    전부 NULL로 남았다 — "적재는 됐는데 표·산점도가 빈다"의 원인.
    """
    import duckdb

    dbp = tmp_path / "et.duckdb"
    rows, filled = _reload(dbp, _long({"Vt": 0.5}), tmp_path, "a.parquet")
    assert (rows, filled) == (1, 0)

    # 같은 포인트 + item 하나 추가 (리포메터에 item을 더한 상황)
    rows, filled = _reload(dbp, _long({"Vt": 0.5, "Ion": 12.0}),
                           tmp_path, "b.parquet")
    assert (rows, filled) == (0, 1)              # 행은 안 늘고 칸만 찼다

    con = duckdb.connect(str(dbp), read_only=True)
    got = con.execute('SELECT "Vt", "Ion" FROM et_data').fetchall()
    con.close()
    assert got == [(0.5, 12.0)]


def test_refill_does_not_overwrite_or_double_count(tmp_path):
    """이미 값이 있는 칸은 그대로, 채울 것이 없으면 0. 멱등이다. (§6)"""
    import duckdb

    dbp = tmp_path / "et.duckdb"
    _reload(dbp, _long({"Vt": 0.5}), tmp_path, "a.parquet")
    # 같은 item을 다른 값으로 다시 적재해도 예전 값이 이긴다(중복 적재 = 무시)
    assert _reload(dbp, _long({"Vt": 9.9}), tmp_path, "b.parquet") == (0, 0)

    con = duckdb.connect(str(dbp), read_only=True)
    assert con.execute("SELECT \"Vt\" FROM et_data").fetchall() == [(0.5,)]
    con.close()


def test_other_step_seq_stays_a_separate_row(tmp_path):
    """seq가 다르면 다른 포인트다 — 채우는 게 아니라 행이 늘어야 한다. (§6)"""
    dbp = tmp_path / "et.duckdb"
    _reload(dbp, _long({"Vt": 0.5}, seq=1), tmp_path, "a.parquet")
    assert _reload(dbp, _long({"Ileak": 1e-9}, seq=2),
                   tmp_path, "b.parquet") == (1, 0)


# ── §14 summary 표의 규격 열 ───────────────────────────────────────
def _td(rows, cat_names=("CAT2",)):
    from etreport.render.pptgen import TableData

    return TableData("DC", [("PA1", ["01", "02"])], rows,
                     cat_names=list(cat_names))


def test_spec_columns_sit_between_item_and_wafer():
    """규격은 item 바로 뒤·wafer 앞이다 — 값보다 기준을 먼저 본다. (§14)"""
    td = _td([{"cats": ["누설"], "item": "Ioff", "spec": ["", "0.5", "1.0"],
               "values": [1, 2], "offspec": [False, False]}])
    assert td.labels() == ["CAT2", "item", "LSL", "Target", "USL"]
    assert td.label_values(td.rows[0]) == ["누설", "Ioff", "", "0.5", "1.0"]


def test_spec_columns_vanish_when_nothing_has_a_spec():
    """규격이 하나도 없으면 빈 열로 wafer를 밀어내지 않는다. (§14)"""
    td = _td([{"cats": ["누설"], "item": "Ioff", "spec": ["", "", ""],
               "values": [1, 2], "offspec": [False, False]}])
    assert td.labels() == ["CAT2", "item"]
    assert td.label_values(td.rows[0]) == ["누설", "Ioff"]
    # spec 키가 아예 없는 예전 표도 그대로 돈다
    old = _td([{"cats": ["누설"], "item": "Ioff",
                "values": [1, 2], "offspec": [False, False]}])
    assert old.label_values(old.rows[0]) == ["누설", "Ioff"]


def test_spec_columns_are_not_merged_vertically():
    """세로 병합은 CAT까지만 — item·규격은 행마다 다르다. (§14)"""
    from etreport.render.pptgen import label_widths_in

    td = _td([{"cats": ["누설"], "item": "Ioff", "spec": ["0.1", "0.5", "1.0"],
               "values": [1, 2], "offspec": [False, False]}])
    n_lab, n_spec = len(td.labels()), len(td.spec_labels())
    assert (n_lab, n_spec) == (5, 3)
    assert len(label_widths_in(n_lab, n_spec)) == n_lab   # 폭도 열 수와 맞는다


def test_build_table_fills_the_spec_from_the_reformatter(qapp, win):
    """화면 표의 규격 값은 리포메터 SPECLOW/SPECHIGH에서 온다. (§14)"""
    from etreport.export.excel import SummaryOptions, build_table, spec_cells

    st = win.anal_ws.state
    cat1 = st.report.table_rows[0].cat1
    td = build_table(st, cat1, SummaryOptions())
    assert all("spec" in r for r in td.rows)
    for r in td.rows:
        assert r["spec"] == spec_cells(st.rf.by_alias.get(r["item"]))


# ── §7 x·y가 같은 행에 없을 때 ─────────────────────────────────────
def test_scatter_says_why_it_is_empty(tmp_path):
    """x·y가 각각은 있는데 같은 행에 없으면 그 이유를 그림에 적는다. (§7)"""
    import polars as pl

    from etreport.render.mpl_renderer import _unpaired

    seq_split = pl.DataFrame({"Vt": [0.5, None], "Ion": [None, 12.0]})
    assert _unpaired({"g1": seq_split}, [("Vt", "Ion")])
    # 한 행에라도 함께 있으면 참견하지 않는다
    ok = pl.DataFrame({"Vt": [0.5, None], "Ion": [1.0, 12.0]})
    assert not _unpaired({"g1": ok}, [("Vt", "Ion")])
    # 아예 비어 있는 것은 "안 합쳐졌다"가 아니다 — 문구를 띄우지 않는다
    empty = pl.DataFrame({"Vt": [None, None], "Ion": [None, 1.0]},
                         schema={"Vt": pl.Float64, "Ion": pl.Float64})
    assert not _unpaired({"g1": empty}, [("Vt", "Ion")])


# ── §8 PPT 공통 범례 · 정사각형 plot ───────────────────────────────
def test_slots_are_square_and_leave_room_for_the_legend():
    """plot은 정사각형, 아래 한 줄은 공통 범례 자리다. (§8)"""
    from pptx import Presentation
    from pptx.util import Inches

    from etreport.render import pptgen

    prs = Presentation()
    prs.slide_width, prs.slide_height = (Inches(pptgen.BASE_W_IN),
                                         Inches(pptgen.BASE_H_IN))
    legend_h = Inches(pptgen.LEGEND_H_IN)
    rects = [pptgen._slot_rect(prs, i, legend_h) for i in range(6)]
    for _left, top, w, h in rects:
        assert w == h                                   # 정사각형
        assert top + h <= prs.slide_height - Inches(pptgen.MARGIN_IN) - legend_h
    assert rects[0][1] < rects[3][1]                    # 1·2·3 윗줄 / 4·5·6 아랫줄
    assert rects[0][0] < rects[1][0] < rects[2][0]      # 왼 → 오


def test_renderer_can_hold_the_legend_back():
    """PPT 슬롯은 범례를 끄고 그린다 — 페이지에 한 벌만 붙이려고. (§8)"""
    import polars as pl

    from etreport.model.specs import PlotSpec
    from etreport.render import mpl_renderer
    from etreport.render.mpl_renderer import GroupStyle

    class _RF:
        by_alias: ClassVar[dict] = {}

    spec = PlotSpec(type="scatter", x="Vt", y="Ion", mode="site")
    data = {"g1": pl.DataFrame({"Vt": [0.5, 0.6], "Ion": [1.0, 2.0]})}
    styles = [GroupStyle(gid="g1", name="A조건", color="#3E51C4",
                         symbol="o", size=26, visible=True)]
    args = (spec, data, styles, _RF(), [], (4.0, 4.0))
    assert mpl_renderer.render(*args).axes[0].get_legend() is not None
    assert mpl_renderer.render(*args, legend=False).axes[0].get_legend() is None


# ── §10 예약 실행이 진짜로 도는가 ──────────────────────────────────
def test_scheduled_run_extracts_and_loads_for_real(tmp_path, appdata):
    """`--run-extract`가 추출→리포메팅→적재를 끝까지 하고 0으로 끝난다. (§10)

    가짜인 것은 bdq 응답 하나뿐이다 — 프리셋 조회·SQL 조립·청크 분할·리포메팅·
    DuckDB 적재는 작업 스케줄러가 부를 때와 같은 코드가 돈다.
    """
    import duckdb

    from etreport import demo_bundle, demo_data, demo_sources
    from etreport.config.settings import Condition, ExtractPreset, Settings
    from etreport.schedule import run_headless

    demo_sources.install(str(tmp_path / "s3"))
    try:
        bundle = demo_bundle.build(tmp_path / "bundle")
        db = tmp_path / "예약.duckdb"
        s = Settings.defaults()
        s.extract_presets = [ExtractPreset(
            name="야간 추출", db_path=str(db),
            reformatter_path=str(bundle.rf_csv),
            conditions=[Condition("line_id", demo_data.LINE_ID, required=True)])]
        s.save()

        # `--days N`은 **오늘 기준**이라, 데모 기간까지 닿도록 N을 잡는다
        span = (date.today() - demo_data.D_FROM).days + 1
        assert run_headless("야간 추출", days=span) == 0
    finally:
        demo_sources.uninstall()

    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM et_data").fetchone()[0] > 0
    finally:
        con.close()


# ── §4 조건 모드 — LIKE와 부등호 ──────────────────────────────────
def _typed_cat() -> Catalog:
    from etreport.config.catalog import ColumnInfo
    c = Catalog()
    c.columns = [ColumnInfo("root_lot_id", "STRING"), ColumnInfo("vt", "FLOAT")]
    return c


def test_like_mode_uses_the_value_as_a_pattern():
    """LIKE 모드는 `%`·`_`를 손으로 적는다 — `*` 치환과 섞이지 않는다. (§4)"""
    from etreport.config.settings import Condition
    from etreport.data.querybuilder import condition_sql

    sql = condition_sql(Condition("root_lot_id", "%PA1% PB2", mode="like"),
                        _typed_cat())
    assert sql == ("(root_lot_id LIKE '%PA1%' OR root_lot_id LIKE 'PB2')")


def test_comparison_mode_prefixes_bare_numbers():
    """부등호 모드는 맨 숫자에만 붙고, 직접 적은 조건은 그대로다. (§4)"""
    from etreport.config.settings import Condition
    from etreport.data.querybuilder import condition_sql

    cat = _typed_cat()
    assert condition_sql(Condition("vt", "25", mode=">="), cat) == "vt >= 25"
    # 범위·`!`·직접 적은 부등호는 모드와 무관
    assert condition_sql(Condition("vt", "1~2", mode="<"), cat) == \
        "vt BETWEEN 1 AND 2"
    # 모드가 없으면 예전과 글자 하나까지 같다
    assert condition_sql(Condition("vt", "25 85"), cat) == "vt IN (25, 85)"


def test_mode_combo_follows_the_column_type(qapp):
    """문자열엔 LIKE, 숫자엔 부등호가 보이고 타입이 바뀌면 되돌아간다. (§4)"""
    from etreport.config.settings import Condition
    from etreport.ui.data_ws import ConditionRow

    cat = _typed_cat()
    row = ConditionRow(Condition("root_lot_id", "PA1"), cat)
    modes = [row.cmb_mode.itemData(i) for i in range(row.cmb_mode.count())]
    assert modes == ["auto", "regexp", "like"]

    row.cond.col = "vt"
    row._sync()
    modes = [row.cmb_mode.itemData(i) for i in range(row.cmb_mode.count())]
    assert modes == ["auto", ">=", ">", "<=", "<"]

    # 숫자에서 고른 부등호는 문자열 컬럼으로 돌아가면 살아 있으면 안 된다
    row.cmb_mode.setCurrentIndex(modes.index(">="))
    assert row.cond.mode == ">="
    row.cond.col = "root_lot_id"
    row._sync()
    assert row.cond.mode == "auto"


# ── §1 inline 계측 [분석에 활용] ──────────────────────────────────
def test_metrology_columns_join_the_scatter_axis_candidates():
    """계측 열은 산점도 x·y 후보다 — 없으면 골라 쓸 수 없다. (§1)"""
    import polars as pl

    from etreport.model import categories as cat
    from etreport.model.state import AppState

    st = AppState()
    st.data = pl.DataFrame({"key": ["k1"], "lot": ["L1"], "wafer": ["01"],
                            "Vt": [0.5], "M1::CD_A": [10.0]})
    st.met_columns = ["M1::CD_A"]
    assert "M1::CD_A" in st.value_columns()
    # 숫자라 boxplot 범주로는 못 쓴다 — 목록에도 넣지 않는다(`is_category`와 일치)
    assert "M1::CD_A" not in cat.choices(st.data, st.met_columns)
    assert not cat.is_category("M1::CD_A", st.data)


def test_attached_columns_survive_reapply():
    """[적용]으로 프레임을 다시 만들어도 계측·tracking 열이 다시 붙는다. (§1·§2)

    예전에는 이름만 `state`에 남고 컬럼은 사라져서, 축 후보에는 보이는데
    아무 데도 반영되지 않았다.
    """
    import polars as pl

    from etreport.data import loader
    from etreport.model.state import AppState
    from tests.test_metrology import ROW

    st = AppState()
    fresh_from_db = pl.DataFrame({"key": ["k1"], "lot": ["PA100"],
                                  "wafer": ["W01"], "Vt": [0.5]})
    st.track_frame = pl.DataFrame({"lot": ["PA100"], "wafer": ["W01"],
                                   "SPLIT": ["A"]})
    st.track_columns = ["SPLIT"]
    st.met_frame = pl.DataFrame([{**ROW, "subitem_id": "Q2",
                                  "fab_value": 12.0}])
    st.met_level = "wafer"

    out = loader.reattach_sources(fresh_from_db, st)

    assert out["SPLIT"][0] == "A"
    assert out["M1::CD_A"][0] == 12.0        # wafer 표기가 달라도 붙는다(01↔W01)


# ── §2·§3 fab tracking 창 ─────────────────────────────────────────
def test_track_columns_come_back_when_the_dialog_reopens(qapp, appdata):
    """창을 닫았다 다시 열면 지난번 컬럼 정의가 그대로 보인다. (§2)"""
    import polars as pl

    from etreport.data import fabtracking as ft
    from etreport.model.state import AppState
    from etreport.ui.widgets.fabtrack_dialog import COL_NAME, FabTrackDialog

    st = AppState()
    st.data = pl.DataFrame({"key": ["k1"], "lot": ["PA100"], "wafer": ["01"]})
    st.track_specs = [ft.TrackColumn(name="주입량", source="ppid", step="300")]

    dlg = FabTrackDialog(st, None)
    try:
        assert dlg.tbl_cols.rowCount() == 1
        assert dlg.tbl_cols.item(0, COL_NAME).text() == "주입량"
        assert dlg.spec_columns()[0].step == "300"
    finally:
        dlg.close()


def test_tracking_table_columns_are_step_seq():
    """열은 step_seq로 갈리고, 값이 모두 같은 step_seq는 빠진다. (§3)"""
    import polars as pl

    from etreport.data import fabtracking as ft
    from tests.test_fabtracking import ROW

    rows = [
        # seq 100(PHOTO): wafer마다 reticle이 다르다 → 남는다
        {"wafer_id": "01", "step_seq": 100, "area": "PHOTO", "reticle_id": "RT_A"},
        {"wafer_id": "02", "step_seq": 100, "area": "PHOTO", "reticle_id": "RT_B"},
        # seq 200(ETCH): ppid가 같다 → 빠진다
        {"wafer_id": "01", "step_seq": 200, "area": "ETCH", "ppid": "E_STD"},
        {"wafer_id": "02", "step_seq": 200, "area": "ETCH", "ppid": "E_STD"},
        # seq 300(ETCH): ppid가 갈린다 → 남는다
        {"wafer_id": "01", "step_seq": 300, "area": "ETCH", "ppid": "I_LOW"},
        {"wafer_id": "02", "step_seq": 300, "area": "ETCH", "ppid": "I_HI"},
    ]
    # process_id는 셋 다 같다 — 예전 키(process_id)였다면 한 열로 뭉쳤을 것이다
    df = pl.DataFrame([{**ROW, "process_id": "SAME", **r} for r in rows])

    assert ft.split_steps(df) == ["100", "300"]
    wide = ft.derive(df, ft.suggest_columns(df))
    assert wide.columns == ["lot", "wafer", "100", "300"]
    assert wide["100"].to_list() == ["RT_A", "RT_B"]     # PHOTO = reticle_id
    assert wide["300"].to_list() == ["I_LOW", "I_HI"]    # 그 외 = ppid


# ── 실험 조건 연결 해제 ────────────────────────────────────────────
def test_clearing_the_split_source_drops_the_split_and_its_groups(appdata):
    """출처를 비우고 [적용]하면 실험 조건도 factor 그룹도 함께 떨어진다.

    예전에는 `state.split`이 그대로 남아 "한번 설정하면 계속 남아 있다"가 됐다.
    손으로 만든 그룹(split에서 나오지 않은 gid)은 살아남아야 한다.
    """
    from etreport import demo
    from etreport.config.settings import AnalysisConfig
    from etreport.model import session
    from etreport.model.specs import GroupStyle
    from etreport.model.state import AppState

    st = AppState()
    demo.load_demo(st)
    assert st.split is not None and st.factors
    mine = GroupStyle(gid="손수", name="손수", color="#000000")
    st.groups = [*st.groups, mine]
    n_split = len(st.groups) - 1

    session.apply_config(st, AnalysisConfig(name="빈 설정"))
    assert st.split is None
    assert st.factors == []
    assert st.groups == [mine]                    # split 그룹만 걷힌다
    assert n_split > 0
