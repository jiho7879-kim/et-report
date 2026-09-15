"""§5.2 탐색 탭이 인스펙터에 넘기는 카드 — 축 / 스케일·범위.

확정 사양:
  - 스케일·범위: X/Y 각각 자동·log·선형, 범위 직접 지정
    (**켜면 현재 축 값을 자동으로 채워 준다**)

그룹 스타일 카드는 여기 없다 — 보이기·색·심볼·REF·편집은 인스펙터 공용
`[그룹]` 섹션 하나가 갖는다(설계 §1 규칙 2). 그 계약은
`tests/test_group_section.py`가 지킨다.
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
    """탭이 인스펙터에 넘기는 섹션의 제목.

    카드는 탭 안이 아니라 **워크스페이스가 소유한 인스펙터**에 들어간다
    (설계 §3). 그래서 `tab.findChildren`이 아니라 탭이 넘기는 목록을 본다.
    """
    from PySide6.QtWidgets import QLabel
    out = []
    for sec in tab.inspector_sections():
        out += [w.text() for w in sec.findChildren(QLabel)
                if w.objectName() == "cardTitle"]
    return out


def test_explore_hands_over_two_cards(tab):
    """탐색 전용은 둘뿐 — 그룹은 탭이 아니라 공용 섹션이 갖는다."""
    assert _card_titles(tab) == ["축", "스케일 · 범위"]


# ── 스케일 ───────────────────────────────────────────────────
@pytest.mark.parametrize("axis,pick,mode", [("x", "log", "log"),
                                            ("y", "log", "log"),
                                            ("y", "선형", "linear"),
                                            ("x", "자동", "auto")])
def test_scale_combo_drives_the_axis(tab, axis, pick, mode):
    tab.cmb_scale[axis].setCurrentText(pick)
    assert qt_until(
        lambda: getattr(tab.state.explore, f"log{axis}_mode") == mode)
    tab.redraw()                       # [그리기] — 지연 규약상 버튼으로 그린다
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
    tab.redraw()                       # [그리기] — 지연 규약상 버튼으로 그린다

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
