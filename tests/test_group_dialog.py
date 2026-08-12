"""§9.1 그룹 편집 — 4단 연쇄 필터 · [적용] 없이 조회 · 배정 범위 · lot 탐색.

확정 사양:
  - lot → step_id → total_site_cnt → temperature 순으로 좁힌다. 앞을 바꾸면
    뒤 콤보 목록과 유효 wafer가 다시 채워진다
  - **배정도 필터 범위에만 적용**한다 (같은 wafer라도 step·온도가 다르면
    다른 측정점이므로)
  - **[조회]는 [적용] 없이도 동작**한다 — DB 경로만 있으면 그 자리에서 읽기
    전용으로 열어 조회한다
  - lot 찾기: 정확히 일치 → 대소문자 무시 → 부분 일치, 없으면 비슷한 lot 제시
"""
from __future__ import annotations

import os
from datetime import datetime

import polars as pl
import pytest

from etreport.data import db, loader
from etreport.model.state import AppState

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = {"line_id": "L1", "root_lot_id": "PA100", "wafer_id": "01",
        "chip_x_pos": 1, "chip_y_pos": 1, "temperature": 25.0,
        "step_id": "M2", "step_seq": 1, "total_site_cnt": 9,
        "tkout_time": datetime(2026, 8, 4, 9, 0)}


@pytest.fixture
def db_path(tmp_path):
    """lot 2개 × wafer 2장 × step 2종 × 온도 2종 — 필터가 실제로 좁혀지도록."""
    rows = []
    for lot in ("PA100", "PB200"):
        for wafer in ("01", "02"):
            for step, temp, site in (("M2", 25.0, 9), ("M2", 85.0, 9),
                                     ("M5", 25.0, 13)):
                for chip in (1, 2):
                    rows.append({**BASE, "root_lot_id": lot, "wafer_id": wafer,
                                 "step_id": step, "temperature": temp,
                                 "total_site_cnt": site, "chip_x_pos": chip,
                                 "item_id": "Vt", "et_value": 0.4})
    p = tmp_path / "groups.parquet"
    pl.DataFrame(rows).write_parquet(p)
    out = tmp_path / "et.duckdb"
    store = db.Store(out)
    try:
        db.pivot_and_load(store, [p])
    finally:
        store.close()
    return str(out)


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


def _texts(cmb) -> list[str]:
    return [cmb.itemText(i) for i in range(cmb.count())]


# ── 조건 컬럼이 분석 프레임에 실린다 ─────────────────────────
def test_context_columns_are_loaded_but_are_not_items(db_path, appdata):
    st = AppState()
    loader.load_state(st, db_path)

    for col in ("step", "temp", "site"):
        assert col in st.data.columns
    assert loader.item_columns(st.data) == ["Vt"]      # 조건은 item이 아니다
    assert sorted(set(st.data["step"])) == ["M2", "M5"]


# ── [적용] 없이 조회 ─────────────────────────────────────────
def test_wafer_index_without_apply(db_path):
    """★ DB 경로만 있으면 [적용] 없이도 조회된다."""
    idx = loader.wafer_index_from_db(db_path)

    assert set(idx.columns) == {"lot", "wafer", "step", "temp", "site", "n"}
    assert sorted(set(idx["lot"])) == ["PA100", "PB200"]
    assert sorted(set(idx["step"])) == ["M2", "M5"]
    assert int(idx.filter((pl.col("lot") == "PA100")
                          & (pl.col("wafer") == "01")
                          & (pl.col("step") == "M5"))["n"].sum()) == 2


def test_dialog_queries_db_when_nothing_applied(qapp, db_path, appdata):
    """★ state.data가 없어도(=[적용] 전) 창이 열리고 lot·필터가 채워진다."""
    st = AppState()                       # data=None, db_path=""
    dlg = _dialog(st, db_path=db_path)

    assert not dlg.index.is_empty()
    assert dlg.ed_lot.text() == "PA100"           # 첫 lot로 바로 조회
    assert _texts(dlg.filters["step"]) == ["전체", "M2", "M5"]
    assert "유효 2장" in dlg.lbl_valid.text()
    assert "12포인트" in dlg.lbl_valid.text().replace(",", "")


# ── 4단 연쇄 필터 ────────────────────────────────────────────
def test_filters_chain_and_counts_follow(qapp, db_path, appdata):
    """★ 앞 단계를 좁히면 뒤 콤보 목록과 유효 wafer·포인트가 따라 바뀐다."""
    st = AppState()
    dlg = _dialog(st, db_path=db_path)

    dlg.filters["step"].setCurrentText("M5")      # step을 좁히면
    assert _texts(dlg.filters["site"]) == ["전체", "13"]      # site 목록이 줄고
    assert _texts(dlg.filters["temp"]) == ["전체", "25.0"]    # temp도 줄고
    assert "유효 2장" in dlg.lbl_valid.text()
    assert "4포인트" in dlg.lbl_valid.text()

    dlg.filters["step"].setCurrentText("M2")
    assert _texts(dlg.filters["temp"]) == ["전체", "25.0", "85.0"]
    assert "8포인트" in dlg.lbl_valid.text()

    dlg.filters["temp"].setCurrentText("85.0")
    assert "4포인트" in dlg.lbl_valid.text()


def test_lot_lookup_widens_step_by_step(qapp, db_path, appdata):
    """정확히 일치 → 대소문자 무시 → 부분 일치. 없으면 비슷한 lot을 보여준다."""
    st = AppState()
    dlg = _dialog(st, db_path=db_path)

    for typed, found in (("PB200", "PB200"), ("pb200", "PB200"),
                         ("B20", "PB200")):
        dlg.ed_lot.setText(typed)
        dlg._lookup()
        assert dlg._lot == found
        assert dlg.ed_lot.text() == found          # 찾은 이름으로 맞춰 준다

    dlg.ed_lot.setText("ZZ999")
    dlg._lookup()
    assert dlg._lot is None
    assert "없음" in dlg.lbl_valid.text() and "비슷한 lot" in dlg.lbl_valid.text()


# ── 배정은 필터 범위에만 ─────────────────────────────────────
def test_assignment_is_scoped_to_the_filter(qapp, db_path, appdata):
    """★ 같은 wafer라도 다른 step·온도의 측정점은 배정되지 않는다."""
    from etreport.model.specs import GroupStyle

    st = AppState()
    loader.load_state(st, db_path)
    st.groups = [GroupStyle(gid="g0", name="Split_A")]
    dlg = _dialog(st, db_path=db_path)
    dlg.ed_lot.setText("PA100")
    dlg._lookup()
    dlg.filters["step"].setCurrentText("M5")

    dlg._assign(["01"], "g0")

    got = st.data.filter(pl.col("gid") == "g0")
    assert got.height == 2                                  # M5 · wafer 01만
    assert set(got["step"]) == {"M5"} and set(got["wafer"]) == {"01"}
    same_wafer_m2 = st.data.filter((pl.col("lot") == "PA100")
                                   & (pl.col("wafer") == "01")
                                   & (pl.col("step") == "M2"))
    assert same_wafer_m2["gid"].to_list() == [""] * same_wafer_m2.height


def test_manual_assignment_survives_reapply(qapp, db_path, appdata):
    """[적용]으로 DB를 다시 읽어도 손으로 짠 그룹이 남는다."""
    from etreport.model.specs import GroupStyle

    st = AppState()
    loader.load_state(st, db_path)
    st.groups = [GroupStyle(gid="g0", name="Split_A")]
    dlg = _dialog(st, db_path=db_path)
    dlg.ed_lot.setText("PA100")
    dlg._lookup()
    dlg._assign(["01", "02"], "g0")          # 필터 없이 = 조건 무관
    before = st.data.filter(pl.col("gid") == "g0").height

    loader.load_state(st, db_path)           # [적용] 다시 누른 셈

    assert st.data.filter(pl.col("gid") == "g0").height == before > 0


def test_unassign_clears_only_that_scope(qapp, db_path, appdata):
    from etreport.model.specs import GroupStyle

    st = AppState()
    loader.load_state(st, db_path)
    st.groups = [GroupStyle(gid="g0", name="Split_A")]
    dlg = _dialog(st, db_path=db_path)
    dlg.ed_lot.setText("PA100")
    dlg._lookup()
    dlg._assign(["01", "02"], "g0")
    dlg.filters["step"].setCurrentText("M5")
    dlg._assign(["01"], "")                  # M5 · wafer 01만 미배정으로

    lot01 = (pl.col("lot") == "PA100") & (pl.col("wafer") == "01")
    assert set(st.data.filter(lot01 & (pl.col("step") == "M5"))["gid"]) == {""}
    assert set(st.data.filter(lot01 & (pl.col("step") == "M2"))["gid"]) == {"g0"}


def test_apply_manual_groups_last_write_wins():
    """같은 범위에 두 번 배정하면 나중 것이 이긴다."""
    df = pl.DataFrame({"lot": ["A", "A"], "wafer": ["01", "02"],
                       "gid": ["", ""], "step": ["M2", "M2"],
                       "temp": ["25.0", "25.0"], "site": ["9", "9"]})
    groups = {("A", "01", None, None, None): "g0",
              ("A", "01", "M2", None, None): "g1"}

    out = loader.apply_manual_groups(df, groups)

    assert out["gid"].to_list() == ["g1", ""]
