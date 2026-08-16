"""§5.2 탐색 탭 오른쪽 패널 — 축 / 스케일·범위 / 그룹 스타일 세 카드.

확정 사양:
  - 스케일·범위: X/Y 각각 자동·log·선형, 범위 직접 지정
    (**켜면 현재 축 값을 자동으로 채워 준다**)
  - 그룹 스타일: 그룹 선택 → 색·심볼·크기·REF 지정
  - 동기화 주의: 스타일은 도크·그룹편집·탐색 세 곳에서 바뀔 수 있으므로
    시그널 한 방향(`groups_changed`)으로 모으고, 컨트롤을 갱신할 때는 시그널을
    막아 재귀를 피한다
"""
from __future__ import annotations

import os
import time as _time

import pytest

from tests.conftest import qt_until

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _wait(qapp_cls, loop_cls, ms: int = 400) -> None:
    """콤보 핸들러는 팝업을 닫고 60ms 뒤에 돈다 — 그때까지 루프를 돌린다."""
    end = _time.monotonic() + ms / 1000
    while _time.monotonic() < end:
        qapp_cls.processEvents(loop_cls.AllEvents, 20)



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
def tab(qapp, appdata):
    from etreport import demo
    from etreport.model.state import AppState, StateBus
    from etreport.ui.tabs.explore import ExploreTab

    st = AppState()
    demo.load_demo(st)
    t = ExploreTab(st, StateBus())
    t.redraw()
    yield t
    t.deleteLater()


def _settle(qapp=None, until=None):
    """콤보 핸들러는 팝업이 닫히도록 한 박자 미뤄 실행된다(common.defer).

    테스트에서는 이벤트 루프를 돌려 그 실행을 기다린다. **조건을 주면 그때까지**
    기다린다 — 지연 실행 앞에 그리기가 줄 서 있으면 고정 시간(400ms)으로는
    데이터 양에 따라 들쭉날쭉해진다(데모 데이터가 커지자 실제로 어긋났다).
    """
    if until is not None:
        assert qt_until(until), "지연 처리가 제때 돌지 않았습니다"
        return
    from PySide6.QtCore import QCoreApplication, QEventLoop
    _wait(QCoreApplication, QEventLoop)


def _card_titles(tab) -> list[str]:
    from PySide6.QtWidgets import QLabel
    return [w.text() for w in tab.findChildren(QLabel)
            if w.objectName() == "cardTitle"]


def test_three_cards_exist(tab):
    assert _card_titles(tab) == ["축", "스케일 · 범위", "그룹 스타일"]


# ── 스케일 ───────────────────────────────────────────────────
@pytest.mark.parametrize("axis,pick,mode", [("x", "log", "log"),
                                            ("y", "log", "log"),
                                            ("y", "선형", "linear"),
                                            ("x", "자동", "auto")])
def test_scale_combo_drives_the_axis(tab, axis, pick, mode):
    tab.cmb_scale[axis].setCurrentText(pick)
    assert qt_until(
        lambda: getattr(tab.state.explore, f"log{axis}_mode") == mode)
    ax = tab.canvas.figure.axes[0]
    scale = ax.get_xscale() if axis == "x" else ax.get_yscale()
    if mode == "log":
        assert scale == "log"
    elif mode == "linear":
        assert scale == "linear"


# ── 범위 직접 지정 ───────────────────────────────────────────
def test_manual_range_prefills_current_axis_values(tab):
    """★ 켜는 순간 지금 보이는 축 값이 채워진다 (빈칸에서 시작하지 않는다)."""
    before = tab._current_limits()

    tab.chk_manual.setChecked(True)

    st = tab.state.explore
    assert st.range_mode == "manual"
    assert st.xmin == pytest.approx(before["x"][0])
    assert st.xmax == pytest.approx(before["x"][1])
    assert st.ymin == pytest.approx(before["y"][0])
    for name in ("xmin", "xmax", "ymin", "ymax"):
        assert tab.ed_range[name].text()          # 입력칸에도 값이 보인다
        assert tab.ed_range[name].isEnabled()


def test_manual_range_is_actually_used_when_drawing(tab):
    tab.chk_manual.setChecked(True)
    tab.ed_range["ymin"].setText("0")
    tab.ed_range["ymax"].setText("2")
    tab._range_edited()

    lo, hi = tab.canvas.figure.axes[0].get_ylim()
    assert (lo, hi) == pytest.approx((0.0, 2.0))


def test_turning_manual_off_returns_to_auto(tab):
    tab.chk_manual.setChecked(True)
    tab.chk_manual.setChecked(False)

    assert tab.state.explore.range_mode == "auto"
    assert not tab.ed_range["xmin"].isEnabled()


def test_non_numeric_range_is_cleared_not_crashing(tab):
    tab.chk_manual.setChecked(True)
    tab.ed_range["xmin"].setText("abc")
    tab._range_edited()

    assert tab.state.explore.xmin is None
    assert tab.ed_range["xmin"].text() == ""


# ── 그룹 스타일 ──────────────────────────────────────────────
def test_style_card_edits_the_group(tab):
    g = tab.state.groups[0]
    tab.cmb_group.setCurrentIndex(0)

    tab.cmb_symbol.setCurrentIndex(1)          # ■ 사각
    assert qt_until(lambda: g.symbol == "s")
    tab.cmb_size.setCurrentText("10")
    assert qt_until(lambda: g.size == 10)


def test_ref_is_exclusive(tab):
    tab.cmb_group.setCurrentIndex(1)
    # 선택이 컨트롤에 반영될 때까지 — REF 체크는 지금 고른 그룹을 가리켜야 한다
    _settle(until=lambda: tab.chk_ref.isChecked() == tab.state.groups[1].ref)
    tab.chk_ref.setChecked(True)

    assert tab.state.groups[1].ref
    assert sum(g.ref for g in tab.state.groups) == 1     # REF는 하나뿐


def test_color_picker_writes_back(tab, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog

    monkeypatch.setattr(QColorDialog, "getColor",
                        staticmethod(lambda *a, **k: QColor("#123456")))
    tab.cmb_group.setCurrentIndex(0)
    _settle(until=lambda: tab.chk_ref.isChecked() == tab.state.groups[0].ref)
    tab._pick_color()

    assert tab.state.groups[0].color == "#123456"


def test_style_change_announces_once_and_does_not_recurse(tab):
    """편집 → groups_changed 한 번. 그 신호를 되받아 다시 편집하지 않는다."""
    seen = []
    tab.bus.groups_changed.connect(lambda: seen.append(1))

    tab.cmb_group.setCurrentIndex(0)
    qt_until(lambda: False, ms=150)            # 그룹 선택 처리를 먼저 소화
    seen.clear()
    tab.cmb_size.setCurrentText("8")
    assert qt_until(lambda: tab.state.groups[0].size == 8)
    assert len(seen) == 1


def test_external_group_change_refreshes_the_combo(tab):
    """도크·그룹편집에서 그룹이 바뀌면 이 카드도 따라온다(§5.2 동기화)."""
    from etreport.model.specs import GroupStyle

    tab.state.groups.append(GroupStyle(gid="gX", name="새 그룹"))
    tab.bus.groups_changed.emit()

    assert [tab.cmb_group.itemText(i) for i in range(tab.cmb_group.count())][-1] \
        == "새 그룹"
    assert tab._stale                      # 다시 그려야 함을 표시만 한다


def test_no_groups_disables_the_controls(qapp, appdata):
    """그룹 0개에서도 인덱스를 직접 쓰지 않는다(§10.9 IndexError 방지)."""
    from etreport.model.state import AppState, StateBus
    from etreport.ui.tabs.explore import ExploreTab

    t = ExploreTab(AppState(), StateBus())

    assert t.cmb_group.count() == 0
    assert not t.cmb_symbol.isEnabled() and not t.btn_color.isEnabled()
    t._style_from_controls()               # 눌러도 죽지 않는다
    t.deleteLater()
