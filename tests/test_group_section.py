"""인스펙터 공용 `[그룹]` 섹션 — 설계 §1 규칙 2 · §3.

예전에는 같은 개념이 세 군데였다: *보이기*=도크 리스트, *스타일*=탐색·리포트
인스펙터에 **각각 한 벌**, *편집*=도크 버튼. 여기서 고정하는 것:

  · 워크스페이스에 그룹 섹션은 **하나뿐**이고 탭이 갖지 않는다.
  · 체크 = 보이기, 고른 행 = 편집 대상(그룹 고르기 콤보는 사라졌다).
  · REF는 하나뿐, 편집은 `groups_changed`를 **한 번만** 쏜다(재귀 없음).
  · 그룹이 0개여도 인덱스를 직접 쓰지 않는다(§10.9 IndexError 방지).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tests.conftest import qt_until


@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def sec(qapp, appdata):
    """데모 상태 위의 섹션 하나 — 탭도 창도 필요 없다."""
    from etreport import demo
    from etreport.model.state import AppState, StateBus
    from etreport.ui.widgets.group_section import GroupSection

    st = AppState()
    demo.load_demo(st)
    s = GroupSection(st, StateBus())
    yield s
    s.deleteLater()


# ── 한 곳에만 있다 ───────────────────────────────────────────────────────
def test_workspace_owns_exactly_one_group_section(qapp, appdata):
    from etreport import demo
    from etreport.config.settings import Settings
    from etreport.model.state import AppState, StateBus
    from etreport.ui.analysis_ws import AnalysisWorkspace
    from etreport.ui.widgets.group_section import GroupSection

    st = AppState()
    demo.load_demo(st)
    ws = AnalysisWorkspace(st, StateBus(), Settings.defaults())
    found = ws.findChildren(GroupSection)
    assert len(found) == 1 and found[0] is ws.group_section
    # 탭은 그룹 위젯을 갖지 않는다 — 인스펙터가 소유한다
    for tab in ws.tab_widgets():
        assert tab.findChildren(GroupSection) == []
        assert not hasattr(tab, "style_card")
    assert ws.inspector.isAncestorOf(ws.group_section)
    ws.deleteLater()


# ── 보이기 · 스타일 ──────────────────────────────────────────────────────
def test_check_toggles_visibility(sec):
    from PySide6.QtCore import Qt

    g = sec.state.groups[0]
    seen: list[int] = []
    sec.bus.groups_changed.connect(lambda: seen.append(1))

    sec.list.item(0).setCheckState(Qt.Unchecked)
    assert g.visible is False
    assert len(seen) == 1
    sec.list.item(0).setCheckState(Qt.Checked)
    assert g.visible is True


def test_selected_row_is_the_edit_target(sec):
    g = sec.state.groups[0]
    sec.select(0)
    assert sec.current() is g

    sec.cmb_symbol.setCurrentIndex(1)          # ■ 사각
    assert qt_until(lambda: g.symbol == "s")
    sec.cmb_size.setCurrentText("10")
    assert qt_until(lambda: g.size == 10)


def test_ref_is_exclusive(sec):
    sec.select(1)
    assert qt_until(lambda: sec.chk_ref.isChecked() == sec.state.groups[1].ref)
    sec.chk_ref.setChecked(True)

    assert sec.state.groups[1].ref
    assert sum(g.ref for g in sec.state.groups) == 1


def test_color_picker_writes_back(sec, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog

    monkeypatch.setattr(QColorDialog, "getColor",
                        staticmethod(lambda *a, **k: QColor("#123456")))
    sec.select(0)
    sec._pick_color()

    assert sec.state.groups[0].color == "#123456"


def test_style_change_announces_once_and_does_not_recurse(sec):
    """편집 → `groups_changed` 한 번. 그 신호를 되받아 다시 편집하지 않는다."""
    seen: list[int] = []
    sec.select(0)
    sec.bus.groups_changed.connect(lambda: seen.append(1))
    sec.cmb_size.setCurrentText("8")

    assert qt_until(lambda: sec.state.groups[0].size == 8)
    assert len(seen) == 1


def test_apply_to_all_shares_symbol_and_size_but_not_colour(sec):
    sec.select(0)
    sec.state.groups[0].symbol, sec.state.groups[0].size = "t", 10

    assert sec.apply_to_all() == len(sec.state.groups)
    assert {(g.symbol, g.size) for g in sec.state.groups} == {("t", 10)}
    # 색은 그룹을 구분하는 값이라 건드리지 않는다
    assert len({g.color for g in sec.state.groups}) > 1


def test_external_group_change_refreshes_the_list(sec):
    """그룹 편집 창·실험 조건에서 그룹이 바뀌면 이 섹션도 따라온다."""
    from etreport.model.specs import GroupStyle

    sec.state.groups.append(GroupStyle(gid="gX", name="새 그룹"))
    sec.bus.groups_changed.emit()

    last = sec.list.item(sec.list.count() - 1).text()
    assert "새 그룹" in last
    assert "3개" in sec.lbl_count.text() or sec.lbl_count.text().endswith("개")


def test_no_groups_disables_the_controls(qapp, appdata):
    """그룹 0개에서도 인덱스를 직접 쓰지 않는다(§10.9)."""
    from etreport.model.state import AppState, StateBus
    from etreport.ui.widgets.group_section import GroupSection

    s = GroupSection(AppState(), StateBus())
    assert s.list.count() == 0
    assert s.current() is None
    assert not s.cmb_symbol.isEnabled() and not s.btn_color.isEnabled()
    s.from_controls()                      # 눌러도 죽지 않는다
    assert s.apply_to_all() == 0
    s.deleteLater()


def test_colour_swatch_label_fits_its_fixed_width(sec, qapp):
    """견본 버튼의 '색'이 잘리지 않는다.

    기본 `QPushButton` 규칙이 좌우 18px씩을 먹어, 폭을 40px로 못 박은 이 버튼은
    스타일을 그대로 두면 글자가 세로 막대 하나로 뭉갠다(설명서 캡처에서 발견).
    `swatch_qss`가 padding을 덮으므로 sizeHint가 고정 폭 안에 들어와야 한다.
    """
    from etreport.ui.widgets.group_section import swatch_qss

    sec.btn_color.setStyleSheet(swatch_qss("#3E51C4"))
    sec.btn_color.ensurePolished()
    hint = sec.btn_color.sizeHint().width()
    assert hint <= sec.btn_color.width(), f"'색'이 잘린다 — sizeHint {hint}px"


def test_confound_warning_sits_next_to_the_groups(sec):
    """혼입은 그룹핑의 결과라 그룹 옆에서 알린다(예전엔 도크였다)."""
    assert sec.lbl_confound is not None
    cf = sec.state.split.confounds(sec.state.factors) if sec.state.split else []
    assert sec.lbl_confound.isVisibleTo(sec) == bool(cf)
