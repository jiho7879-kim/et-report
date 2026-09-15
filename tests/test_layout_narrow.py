"""1366×768 노트북에서 깨지지 않는다 — 설계 §2의 전제.

현장 PC에 1366×768이 섞여 있다. 예전 리포트 탭은 도크 300 + 페이지 목록 158 +
인스펙터 292 = 750을 먹어 캔버스가 616px뿐이었고 plot 6장이 엄지손톱이 됐다.
여기서 고정하는 것:
  · 세 탭 모두 이 폭에서 **가로 스크롤 없이** 조립된다.
  · 고정 폭 패널(왼쪽 레일·인스펙터) 안의 내용이 제 폭 안에 들어간다 —
    가로 스크롤이 없어서 넘친 위젯은 오류 없이 오른쪽이 잘린 채로 남는다.
  · 캔버스가 쓸 폭이 확보되고, 양쪽을 접으면(F9·F10) 거의 전폭이 된다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NARROW = (1366, 768)


@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    from etreport.app import _load_style
    _load_style(app)
    yield app
    app.processEvents()


@pytest.fixture(scope="module")
def win(qapp, tmp_path_factory):
    """창 하나를 모듈 안에서 돌려 쓴다(탭 전환마다 plot을 다시 그리므로)."""
    from _pytest.monkeypatch import MonkeyPatch

    from etreport import demo
    from etreport.config.catalog import Catalog
    from etreport.config.settings import Settings
    from etreport.model.state import AppState
    from etreport.ui.mainwindow import MainWindow

    mp = MonkeyPatch()
    mp.setenv("APPDATA", str(tmp_path_factory.mktemp("appdata")))
    state = AppState()
    demo.load_demo(state)
    w = MainWindow(Settings(), Catalog(), state)
    w.resize(*NARROW)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    mp.undo()


@pytest.mark.parametrize("index", [0, 1, 2])
def test_analysis_assembles_without_horizontal_scroll(qapp, win, index):
    ws = win.anal_ws
    win._switch(1)
    ws.tabs.setCurrentIndex(index)
    qapp.processEvents()
    need = ws.minimumSizeHint().width()
    assert need <= NARROW[0], (
        f"분석 화면이 {need}px를 요구한다 — 1366px에서 오른쪽이 잘린다")


@pytest.mark.parametrize("index", [0, 1, 2])
def test_inspector_content_fits_its_fixed_width(qapp, win, index):
    """도크와 같은 계약 — 인스펙터도 고정 폭 스크롤 영역이다."""
    ws = win.anal_ws
    win._switch(1)
    ws.tabs.setCurrentIndex(index)
    qapp.processEvents()
    panel = ws.inspector.panel()
    need = panel.minimumSizeHint().width()
    room = ws.inspector.viewport().width()
    assert need <= room, (
        f"인스펙터 내용이 {need}px를 요구하는데 쓸 수 있는 폭은 {room}px다 — "
        f"오른쪽이 잘린다")


def test_canvas_keeps_a_usable_width(qapp, win):
    """탭이 쓰는 폭 = 1366 − 레일 − 인스펙터. 예전 리포트 탭의 616px보다 넓어야
    한다(네 번째 칼럼을 없앤 이유)."""
    from etreport.ui.inspector import INSPECTOR_WIDTH
    from etreport.ui.source_rail import RAIL_WIDTH

    ws = win.anal_ws
    win._switch(1)
    qapp.processEvents()
    expected = NARROW[0] - RAIL_WIDTH - INSPECTOR_WIDTH
    assert ws.tabs.width() >= expected - 8
    assert ws.tabs.width() > 616


def test_folding_both_panels_gives_the_canvas_the_screen(qapp, win):
    ws = win.anal_ws
    win._switch(1)
    qapp.processEvents()
    ws.toggle_rail()
    ws.toggle_inspector()
    qapp.processEvents()
    assert ws.tabs.width() >= NARROW[0] - 20
    ws.toggle_rail()                 # 같은 창을 쓰는 다음 검사를 위해 되돌린다
    ws.toggle_inspector()
    qapp.processEvents()


def test_action_bar_height_is_fixed(qapp, win):
    """본문 높이 계산(46+36+42 = 124)의 전제다 — 바가 늘어나면 캔버스가 줄어든다."""
    from etreport.ui.actionbar import ACTION_BAR_HEIGHT

    ws = win.anal_ws
    win._switch(1)
    qapp.processEvents()
    assert ws.actionbar.height() == ACTION_BAR_HEIGHT
