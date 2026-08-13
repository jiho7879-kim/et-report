"""분석 화면 버그 9건 회귀 — 개발 프롬프트(버그수정_9건)에 대응.

1·9 배포 exe에서 터지던 ImportError/TypeError · 2 온도 5단위 보정 ·
3 [적용] 시 자동 그림 금지 · 4 DB 교체 시 재적용 · 5 자동 그룹핑 ·
6 [적용] 후 그룹 초기화 · 8 표에 그룹 반영.
(7 로딩 표시는 UI 동작이라 test_ui_smoke의 모달 가로채기로 확인한다.)
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


def _load_db(tmp_path, rows: list[dict], name: str = "et.duckdb") -> str:
    long = pl.DataFrame([{**BASE, **r} for r in rows])
    p = tmp_path / f"{name}.parquet"
    long.write_parquet(p)
    dbp = tmp_path / name
    store = db.Store(dbp)
    try:
        db.pivot_and_load(store, [p])
    finally:
        store.close()
    return str(dbp)


@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


# ── 1 · 9 배포본에서 터지던 import/시그니처 ──────────────────
def test_session_can_import_table_mode_of():
    """[적용] 경로가 pptgen.table_mode_of를 실제로 부른다(§10.8 'wide' 흡수)."""
    from etreport.config.settings import AnalysisConfig
    from etreport.model.session import apply_config

    st = AppState()
    rep = apply_config(st, AnalysisConfig(name="t", table_slide_mode="wide"))
    assert rep.ok and st.table_slide_mode == "overflow"

    apply_config(st, AnalysisConfig(name="t", table_slide_mode="split"))
    assert st.table_slide_mode == "split"


def test_deckbuild_passes_tables_to_build_deck(tmp_path, appdata):
    """deckbuild.generate가 build_deck(tables=…)로 부른다 — TypeError 회귀."""
    from etreport import demo
    from etreport.export import deckbuild

    st = AppState()
    demo.load_demo(st)
    out = deckbuild.generate(st, str(tmp_path / "deck.pptx"))

    from pptx import Presentation
    assert len(Presentation(out).slides) > 0


# ── 2 온도 5단위 보정 ────────────────────────────────────────
@pytest.mark.parametrize("raw,expect", [(23.9, 25), (24.9, 25), (149.0, 150),
                                        (-40.0, -40), (25.0, 25), (22.5, 25),
                                        (86.2, 85), (None, None)])
def test_temperature_is_rounded_to_five(tmp_path, appdata, raw, expect):
    """★ 23.9 → 25, 149 → 150. NULL은 그대로. 읽는 시점에 보정한다."""
    st = AppState()
    loader.load_state(st, _load_db(
        tmp_path, [{"temperature": raw, "item_id": "Vt", "et_value": 0.4}],
        name=f"t{expect}.duckdb"))

    got = st.data["temp"][0]
    assert (got is None) if expect is None else (int(got) == expect)


def test_raw_temperatures_merge_into_one_point(tmp_path, appdata):
    """23.9와 25.0은 같은 측정점 — 보정이 병합 키에 들어가야 한다(§10.1)."""
    st = AppState()
    loader.load_state(st, _load_db(tmp_path, [
        {"temperature": 23.9, "step_seq": 1, "item_id": "Vt", "et_value": 0.4},
        {"temperature": 25.0, "step_seq": 2, "item_id": "Ioff",
         "et_value": 1e-9, "tkout_time": datetime(2026, 8, 4, 9, 5)},
    ]))

    assert st.data.height == 1
    row = st.data.row(0, named=True)
    assert row["Vt"] is not None and row["Ioff"] is not None
    assert int(row["temp"]) == 25


def test_wafer_index_shows_rounded_temperature(tmp_path, appdata):
    dbp = _load_db(tmp_path, [
        {"temperature": 23.9, "item_id": "Vt", "et_value": 0.4},
        {"temperature": 86.2, "chip_x_pos": 2, "item_id": "Vt", "et_value": 0.5},
    ])
    idx = loader.wafer_index_from_db(dbp)

    assert sorted(idx["temp"].to_list()) == ["25", "85"]


def test_other_columns_are_not_rounded(tmp_path, appdata):
    """보정은 온도에만 — site·step은 그대로."""
    st = AppState()
    loader.load_state(st, _load_db(
        tmp_path, [{"total_site_cnt": 13, "item_id": "Vt", "et_value": 0.4}]))

    assert str(st.data["site"][0]) == "13"
    assert st.data["step"][0] == "M2"


# ── 3 [적용] 시 자동 그림 금지 ───────────────────────────────
def test_mark_stale_does_not_draw(qapp, appdata):
    """★ data_changed(=[적용])는 dirty만 남긴다 — 그리기는 버튼으로."""
    from etreport import demo
    from etreport.model.state import StateBus
    from etreport.ui.tabs.explore import ExploreTab

    st = AppState()
    demo.load_demo(st)
    bus = StateBus()
    tab = ExploreTab(st, bus)
    tab.isVisible = lambda: True          # 창을 실제로 띄우지 않는다(뒤 테스트 오염)
    calls = []
    tab.refresh = lambda: calls.append(1)          # 스파이

    bus.data_changed.emit()

    assert tab._stale and not calls
    tab.deleteLater()


def test_groups_changed_redraws_immediately(qapp, appdata):
    """그룹 토글은 즉시 반영(확정 Q3-2=B)."""
    from etreport import demo
    from etreport.model.state import StateBus
    from etreport.ui.tabs.explore import ExploreTab

    st = AppState()
    demo.load_demo(st)
    bus = StateBus()
    tab = ExploreTab(st, bus)
    tab.isVisible = lambda: True          # 창을 실제로 띄우지 않는다(뒤 테스트 오염)
    calls = []
    tab.refresh = lambda: calls.append(1)

    bus.groups_changed.emit()

    assert calls, "그룹 변경은 보고 있는 탭에서 바로 반영돼야 한다"
    tab.deleteLater()


# ── 5 자동 그룹핑 ────────────────────────────────────────────
def _dialog(state, db_path=""):
    from etreport.ui.widgets.group_dialog import GroupDialog
    return GroupDialog(state, None, db_path=db_path)


@pytest.fixture
def two_wafer_db(tmp_path):
    return _load_db(tmp_path, [
        {"wafer_id": "01", "item_id": "Vt", "et_value": 0.4},
        {"wafer_id": "02", "chip_x_pos": 2, "item_id": "Vt", "et_value": 0.5},
        {"root_lot_id": "PB200", "wafer_id": "01", "chip_x_pos": 3,
         "item_id": "Vt", "et_value": 0.6},
    ])


def test_auto_group_by_wafer(qapp, two_wafer_db, appdata):
    """★ wafer마다 그룹 하나 — 이름은 wafer ID, 첫 그룹이 REF."""
    st = AppState()
    dlg = _dialog(st, db_path=two_wafer_db)

    n = dlg.auto_group("wafer")

    assert n == 3                                   # PA100 2장 + PB200 1장
    assert [g.name for g in st.groups] == ["01", "02", "01"]
    assert st.groups[0].ref and sum(g.ref for g in st.groups) == 1
    assert len(st.manual_groups) == 3
    assert all(k[2:] == (None, None, None) for k in st.manual_groups)
    dlg.deleteLater()


def test_auto_group_by_lot(qapp, two_wafer_db, appdata):
    st = AppState()
    dlg = _dialog(st, db_path=two_wafer_db)

    n = dlg.auto_group("lot")

    assert n == 2 and [g.name for g in st.groups] == ["PA100", "PB200"]
    # lot 전체가 그 그룹에 들어간다
    gid = st.groups[0].gid
    assert sum(1 for k, v in st.manual_groups.items()
               if v == gid and k[0] == "PA100") == 2
    dlg.deleteLater()


def test_auto_group_is_idempotent(qapp, two_wafer_db, appdata):
    st = AppState()
    dlg = _dialog(st, db_path=two_wafer_db)

    dlg.auto_group("wafer")
    first = dict(st.manual_groups)
    dlg.auto_group("wafer")

    assert st.manual_groups == first
    assert len(st.groups) == 3                      # 두 배로 늘지 않는다
    dlg.deleteLater()


# ── 4 DB 교체 시 재적용 ──────────────────────────────────────
def test_switching_db_clears_assignments_and_regroups(qapp, tmp_path, appdata,
                                                      monkeypatch, two_wafer_db):
    """★ 다른 DB를 고르면 이전 배정은 버리고 새 DB 기준으로 자동 배정."""
    from PySide6.QtWidgets import QFileDialog

    other = _load_db(tmp_path, [
        {"root_lot_id": "PC300", "wafer_id": "07", "item_id": "Vt",
         "et_value": 0.9}], name="other.duckdb")

    st = AppState()
    dlg = _dialog(st, db_path=two_wafer_db)
    dlg.auto_group("wafer")
    assert any(k[0] == "PA100" for k in st.manual_groups)

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (other, "")))
    dlg._pick_db()

    assert all(k[0] == "PC300" for k in st.manual_groups), "이전 DB 배정이 남았다"
    assert sorted(set(dlg.index["lot"])) == ["PC300"]
    dlg.deleteLater()


# ── 6 [적용] 후 그룹 초기화 ──────────────────────────────────
def test_manual_groups_survive_apply_without_split(qapp, two_wafer_db, appdata):
    """★ [적용] 전에 짠 그룹이 [적용] 뒤에도 살아 있어야 한다."""
    from etreport.config.settings import AnalysisConfig
    from etreport.model.session import apply_config

    st = AppState()
    dlg = _dialog(st, db_path=two_wafer_db)
    dlg.auto_group("wafer")
    groups_before = [g.gid for g in st.groups]
    dlg.deleteLater()

    apply_config(st, AnalysisConfig(name="t", db_path=two_wafer_db))

    assert [g.gid for g in st.groups] == groups_before
    assert set(st.data["gid"]) - {""} == set(groups_before)


def test_manual_beats_split_assignment(qapp, two_wafer_db, appdata):
    """실험 조건 배정보다 손배정이 이긴다(CLAUDE.md 확정 순서)."""
    from etreport.config.settings import AnalysisConfig
    from etreport.model.session import apply_config

    st = AppState()
    st.manual_groups[("PA100", "01", None, None, None)] = "mine"
    cfg = AnalysisConfig(name="t", db_path=two_wafer_db,
                         split_text="lot,wafer,M1\nPA100,01,Base\nPA100,02,Hi")

    apply_config(st, cfg)

    got = st.data.filter((pl.col("lot") == "PA100")
                         & (pl.col("wafer") == "01"))["gid"].to_list()
    assert set(got) == {"mine"}


# ── 8 표에 그룹 반영 ─────────────────────────────────────────
def _state_with_groups():
    from etreport.model.specs import GroupStyle

    st = AppState()
    st.data = pl.DataFrame({
        "key": ["1", "2", "3"], "lot": ["PA1", "PA1", "PB2"],
        "wafer": ["01", "02", "01"], "gid": ["g0", "g1", ""],
        "Vt": [0.4, 0.5, 0.6]})
    st.groups = [GroupStyle(gid="g0", name="A"), GroupStyle(gid="g1", name="B")]
    return st


def test_table_columns_follow_visible_groups():
    """★ plot과 같은 기준 — visible 그룹의 wafer만, 미배정은 숨김."""
    st = _state_with_groups()
    assert st.wafer_columns() == [("PA1", ["01", "02"])]

    st.groups[1].visible = False                   # B 숨김
    assert st.wafer_columns() == [("PA1", ["01"])]


def test_table_shows_all_when_no_assignment():
    st = _state_with_groups()
    st.data = st.data.with_columns(pl.lit("").alias("gid"))
    assert st.wafer_columns() == [("PA1", ["01", "02"]), ("PB2", ["01"])]


def test_build_table_header_matches_visible_groups(appdata):
    """표의 단일 진실(build_table)도 같은 열을 쓴다."""
    from etreport.export.excel import SummaryOptions, build_table
    from etreport.model.specs import ReportSpec, TableRowSpec

    st = _state_with_groups()
    st.report = ReportSpec(report="R",
                           table_rows=[TableRowSpec("Vt", ["DC"])])
    td = build_table(st, "DC", SummaryOptions())

    assert td.header_lots == [("PA1", ["01", "02"])]
    st.groups[1].visible = False
    assert build_table(st, "DC", SummaryOptions()).header_lots == [
        ("PA1", ["01"])]
