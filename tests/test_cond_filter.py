"""측정 조건 필터(step · site · temp) — 분석 범위를 결과 만들기 **전에** 정한다.

그룹 편집 창의 같은 이름 필터는 *배정 범위*이고 이쪽은 *분석 범위*다. 예전에는
후자가 없어서, 조건을 좁혀 보려면 요약 표 화면에서는 보이지도 않는 그룹 편집
창을 열어야 했다. 여기서 못 박는 것:

  ① 빈 조건이면 프레임이 **글자 하나까지 예전과 같다**
  ② 좁히기는 로딩 한 곳에서만 — `state.data`가 이미 좁혀진 프레임이다
  ③ 콤보 목록은 **좁히기 전** 프레임으로 만든다(아니면 되돌릴 수 없다)
  ④ 조건을 바꿔도 즉시 다시 읽지 않는다(지연 계산 — [적용]이 트리거)
"""
from __future__ import annotations

import polars as pl
import pytest

from etreport.model import conditions as cond


@pytest.fixture
def frame() -> pl.DataFrame:
    return pl.DataFrame({
        "key": ["k1", "k2", "k3", "k4"],
        "lot": ["PA1"] * 4,
        "wafer": ["W01", "W01", "W02", "W02"],
        "step": ["M2ET", "M2ET", "M3ET", "M3ET"],
        "temp": [25.0, 85.0, 25.0, 85.0],
        "site": [9, 9, 5, 5],
        "Vt": [1.0, 2.0, 3.0, 4.0]})


# ── 규칙 ─────────────────────────────────────────────────────
def test_empty_filter_returns_the_same_frame(frame):
    """★ 고르지 않은 상태는 예전과 완전히 같다 — 객체까지 그대로."""
    for empty in (None, {}, {"step": "", "temp": " "}):
        out, dropped = cond.apply(frame, empty)
        assert out is frame and dropped == 0


def test_filter_narrows_and_reports_what_it_dropped(frame):
    out, dropped = cond.apply(frame, {"temp": "25.0"})
    assert out["key"].to_list() == ["k1", "k3"]
    assert dropped == 2

    out, dropped = cond.apply(frame, {"temp": "25.0", "step": "M2ET"})
    assert out["key"].to_list() == ["k1"]
    assert dropped == 3


def test_values_compare_as_text_whatever_the_column_type(frame):
    """temp가 double이든 int든 콤보에 보이는 값이 그대로 걸려야 한다."""
    as_int = frame.with_columns(pl.col("temp").cast(pl.Int64))
    assert cond.choices(as_int)["temp"] == ["25", "85"]
    assert cond.apply(as_int, {"temp": "25"})[0].height == 2
    assert cond.choices(frame)["temp"] == ["25.0", "85.0"]
    assert cond.apply(frame, {"temp": "25.0"})[0].height == 2


def test_choices_are_sorted_like_numbers_not_strings():
    df = pl.DataFrame({"temp": [125.0, 25.0, 85.0], "step": ["b", "a", "c"]})
    assert cond.choices(df)["temp"] == ["25.0", "85.0", "125.0"]
    assert cond.choices(df)["step"] == ["a", "b", "c"]


def test_display_is_tidied_but_the_matched_value_is_not():
    assert cond.pretty("25.0") == "25" and cond.pretty("M2ET") == "M2ET"
    assert cond.pretty("9.5") == "9.5"
    assert cond.label({"step": "M2ET", "temp": "25.0", "site": "9"}) \
        == "step M2ET · site 9 · 25℃"
    assert cond.label({}) == ""                  # '전체'라고 적지 않는다


# ── 로딩이 좁힌다 (②·③) ─────────────────────────────────────
@pytest.fixture
def db(tmp_path):
    """step·temp가 섞인 작은 DuckDB — 리포메팅 → 적재 실제 경로로 만든다."""
    from etreport.data import db as dbm
    from etreport.data.reformatter import apply as rf_apply
    from tests import factory

    rf = factory.make_reformatter(n_real=4, n_addp=1, seed=5)
    items = [r.itemid for r in rf.reals()]
    parts = []
    for i, (step, temp) in enumerate((("M2ET", 25.0), ("M2ET", 85.0),
                                      ("M3ET", 25.0))):
        src = factory.make_long(items, lots=1, wafers=2, chips=2, seed=5 + i)
        parts.append(src.with_columns(
            pl.lit(step).alias("step_id"),
            pl.lit(temp).alias("temperature"),
            pl.lit(f"c{i}").alias("root_lot_id")))
    out = rf_apply(rf, pl.concat(parts))
    f = tmp_path / "raw_20260801_20260801_x.parquet"
    out.write_parquet(f)
    store = dbm.Store(tmp_path / "et.duckdb")
    dbm.pivot_and_load(store, [f])
    store.con.close()
    return str(tmp_path / "et.duckdb")


def test_load_state_narrows_and_keeps_the_full_choice_list(db, appdata):
    """★ 좁힌 것은 `state.data`, 목록은 **좁히기 전** 기준."""
    from etreport.data import loader
    from etreport.model.state import AppState

    st = AppState()
    loader.load_state(st, db)
    full = st.data.height
    assert cond.choices(st.data)["step"] == ["M2ET", "M3ET"]

    st2 = AppState()
    st2.cond_filter = {"step": "M2ET"}
    line = loader.load_state(st2, db)

    assert 0 < st2.data.height < full
    assert set(st2.data["step"].to_list()) == {"M2ET"}
    # 목록은 좁히기 전 프레임에서 왔으므로 M3ET도 남아 있다 — 되돌릴 수 있다
    assert st2.cond_choices["step"] == ["M2ET", "M3ET"]
    assert "조건 step M2ET" in line


def test_load_state_without_a_filter_is_unchanged(db, appdata):
    from etreport.data import loader
    from etreport.model.state import AppState

    a, b = AppState(), AppState()
    b.cond_filter = {}
    assert loader.load_state(a, db) == loader.load_state(b, db)
    assert "조건" not in loader.load_state(a, db)


def test_cond_index_reads_choices_without_applying(db):
    """[적용] 전에도 콤보를 채울 수 있어야 한다 — lot 목록과 같은 관용구."""
    from etreport.data import loader

    got = loader.cond_index(db)
    assert got["step"] == ["M2ET", "M3ET"]
    assert loader.cond_index("없는파일.duckdb") == {}


# ── 레일 (④) ────────────────────────────────────────────────
@pytest.fixture
def demo_state(appdata):
    from etreport import demo
    from etreport.model.state import AppState

    st = AppState()
    demo.load_demo(st)
    return st


@pytest.fixture
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    return QApplication.instance() or QApplication([])


def test_rail_has_the_filter_and_defers_the_reload(qapp, demo_state):
    """★ 조건은 레일(왼쪽)에 있고, 바꾸면 [적용]이 dirty가 될 뿐이다."""
    from etreport.config.settings import Settings
    from etreport.model.state import StateBus
    from etreport.ui.analysis_ws import AnalysisWorkspace

    ws = AnalysisWorkspace(demo_state, StateBus(), Settings.defaults())
    try:
        demo_state.cond_choices = cond.choices(demo_state.data)
        ws._sync_cond()
        assert set(ws.cond_combos) == set(cond.NAMES)
        combo = ws.cond_combos["temp"]
        assert combo.itemData(0) == cond.ALL          # 첫 항목은 '전체'
        assert combo.itemText(1) == "25"              # 보이는 글자는 정리된다

        ws._applied = True
        combo.setCurrentIndex(1)
        ws._cond_changed("temp")

        assert ws.cfg().cond_temp == combo.itemData(1)   # 값은 원본 그대로
        assert not ws._applied                            # 다시 읽지는 않는다
        assert demo_state.data.height > 0                 # 프레임은 그대로
        assert "25℃" in ws.cond_section.toggle.text().upper() or \
               "25" in ws.cond_section.toggle.text()

        ws._cond_clear()
        assert ws.cfg().cond_temp == ""
    finally:
        ws.deleteLater()


def test_group_dialog_can_search_lots(qapp, demo_state):
    """멀티 lot 탭에도 lot 검색 — 숨기기만 하고 체크는 건드리지 않는다."""
    from etreport.ui.widgets.group_dialog import GroupDialog

    dlg = GroupDialog(demo_state)
    try:
        assert dlg.m_lots.count() > 1
        first = dlg.m_lots.item(0).text()
        dlg.m_search.setText(first)
        vis = [dlg.m_lots.item(i).text() for i in range(dlg.m_lots.count())
               if not dlg.m_lots.item(i).isHidden()]
        assert vis == [first]
        # 숨긴 lot의 체크는 그대로다
        assert len(dlg._m_selected_lots()) == dlg.m_lots.count()
        dlg.m_search.setText("")
        assert not any(dlg.m_lots.item(i).isHidden()
                       for i in range(dlg.m_lots.count()))
    finally:
        dlg.deleteLater()
