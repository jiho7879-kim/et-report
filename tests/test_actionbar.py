"""하단 액션바와 인스펙터 골격 — 설계 §1 규칙 2·3, §3.

여기서 고정하는 것:
  · 주 동작 버튼은 탭을 옮겨도 **같은 자리**에 있다(라벨만 바뀐다).
  · 액션바는 탭의 버튼을 **그대로** 담는다 — 프록시를 만들면 dirty 표시(`•`)와
    `Ctrl+Enter`가 두 벌로 갈린다.
  · 결과를 꺼내는 버튼은 오른쪽 끝에 있다(요약표 공유·PPT가 한 번에 닿는다).
  · 인스펙터는 워크스페이스가 하나만 소유하고, 탭이 바뀌면 전용 섹션만 갈린다.
  · F9·F10으로 양쪽 패널이 접힌다(1366×768에서 캔버스를 되찾는 길).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


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
    """창 하나를 모듈 안에서 돌려 쓴다 — 탭을 옮기면 plot 6장을 다시 그리므로
    테스트마다 창을 새로 조립하면 이 파일만 90초가 된다. 여기 검사는 배치와
    주소뿐이고 상태를 망치는 것은 monkeypatch로 되돌린다."""
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
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    mp.undo()


def _goto(qapp, win, index: int):
    win._switch(1)
    win.anal_ws.tabs.setCurrentIndex(index)
    qapp.processEvents()
    return win.anal_ws.tabs.widget(index)


# ── 주 동작 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("index,label", [(0, "그리기"), (1, "표 만들기"),
                                         (2, "미리보기")])
def test_primary_button_is_the_tabs_own_button(qapp, win, index, label):
    """액션바가 보여 주는 버튼이 탭의 주 동작 버튼 **그 객체**여야 한다."""
    tab = _goto(qapp, win, index)
    shown = win.anal_ws.actionbar.primary_of(index)
    assert shown is getattr(tab, tab.stale_button_attr)
    assert label in shown.text()


def test_primary_button_never_moves(qapp, win):
    """탭을 옮겨도 주 동작의 자리는 같다 — 누를 것을 눈으로 찾지 않게."""
    spots = []
    for index in range(3):
        _goto(qapp, win, index)
        btn = win.anal_ws.actionbar.primary_of(index)
        spots.append(btn.mapTo(win.anal_ws.actionbar, btn.rect().topLeft()))
    assert len({(p.x(), p.y()) for p in spots}) == 1, spots


def test_dirty_mark_lands_on_the_bar_button(qapp, win):
    """지연 계산 표시(앰버 + 라벨 끝 `•`)가 액션바의 그 버튼에 찍힌다."""
    from etreport.ui.tabs.common import DIRTY_MARK

    tab = _goto(qapp, win, 0)
    tab.mark_fresh()
    btn = win.anal_ws.actionbar.primary_of(0)
    assert btn.property("dirty") == "false"
    assert not btn.text().endswith(DIRTY_MARK)
    tab.mark_stale()
    assert btn.property("dirty") == "true"
    assert btn.text().endswith(DIRTY_MARK)


def test_ctrl_enter_presses_the_bar_button(qapp, win, monkeypatch):
    """`Ctrl+Enter`는 액션바 왼쪽 끝의 그 버튼을 누른다.

    연결은 건드리지 않는다(버튼은 생성 때 bound method로 묶여 있어 속성을
    바꿔도 끊기지 않는다) — 버튼이 실제로 하는 일의 안쪽만 가로챈다.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    tab = _goto(qapp, win, 1)
    fired: list[int] = []
    monkeypatch.setattr(tab, "_rebuild", lambda: fired.append(1))
    assert win.anal_ws.actionbar.primary_of(1) is tab.btn_build
    QTest.keyClick(win, Qt.Key_Return, Qt.ControlModifier)
    qapp.processEvents()
    assert fired == [1]


# ── 결과를 꺼내는 자리 ───────────────────────────────────────────────────
@pytest.mark.parametrize("index,wanted", [
    (0, ["＋ 리포트에 추가"]),
    (1, ["전체 xlsx 내보내기"]),
    (2, ["템플릿에 저장", "PPT 생성"]),
])
def test_result_buttons_are_on_the_right(qapp, win, index, wanted):
    tab = _goto(qapp, win, index)
    labels = [w.text() for w in tab.action_items().extra]
    assert labels == wanted


def test_status_line_is_the_tabs_own_label(qapp, win):
    """가운데 상태 줄도 탭이 쓰던 그 라벨이다 — 문구가 두 곳이 되지 않게."""
    for index, attr in ((0, "lbl_info"), (1, "lbl_state"), (2, "lbl_excl")):
        tab = _goto(qapp, win, index)
        assert tab.action_items().status is getattr(tab, attr)


# ── 인스펙터 ─────────────────────────────────────────────────────────────
def test_inspector_is_owned_by_the_workspace(qapp, win):
    """탭 안에 인스펙터 패널이 또 있으면 칼럼이 넷이 된다(설계 §0 E)."""
    from etreport.ui.inspector import INSPECTOR_WIDTH

    ws = win.anal_ws
    assert ws.inspector.width() == INSPECTOR_WIDTH
    for tab in ws.tab_widgets():
        assert tab.findChild(type(ws.inspector)) is None


def test_inspector_page_follows_the_tab(qapp, win):
    """탭이 바뀌면 전용 섹션만 갈아 끼운다 — 섹션 위젯은 한 벌로 남는다."""
    ws = win.anal_ws
    for index in range(3):
        tab = _goto(qapp, win, index)
        sections = tab.inspector_sections()
        assert sections, "탭이 인스펙터에 넘기는 섹션이 없다"
        for sec in sections:
            assert sec.isVisible(), (index, sec)
        # 다른 탭의 섹션은 같은 자리에 숨어 있다
        for other in ws.tab_widgets():
            if other is tab:
                continue
            assert not any(s.isVisible() for s in other.inspector_sections())


# ── 패널 접기 ────────────────────────────────────────────────────────────
def test_f9_f10_fold_the_panels(qapp, win):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    ws = win.anal_ws
    win._switch(1)
    qapp.processEvents()
    wide = ws.tabs.width()
    for key in (Qt.Key_F9, Qt.Key_F10):
        QTest.keyClick(win, key)
    qapp.processEvents()
    assert not ws.rail.isVisible()
    assert not ws.inspector.isVisible()
    assert ws.tabs.width() > wide
    for key in (Qt.Key_F9, Qt.Key_F10):
        QTest.keyClick(win, key)
    qapp.processEvents()
    assert ws.rail.isVisible() and ws.inspector.isVisible()
    assert ws.tabs.width() == wide


def test_shortcut_help_mentions_the_new_keys(qapp):
    from etreport.ui.mainwindow import SHORTCUT_TEXT
    assert "F9" in SHORTCUT_TEXT and "F10" in SHORTCUT_TEXT
