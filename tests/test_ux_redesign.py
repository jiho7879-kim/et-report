"""리디자인에서 새로 생긴 UX 규약 — 단축키·상태 레일·미적용 표시·알림.

전부 offscreen에서 실제 창을 조립해 확인한다. 여기서 고정하는 것:
  · F5는 **보고 있는 화면**의 주 동작으로 간다(두 화면에 같은 키가 걸려 있다).
  · Ctrl+Enter는 보고 있는 탭의 주 버튼을 누른다.
  · 상태 레일이 램프(●◐○)로 상태를 말한다 — 색만으로 알리지 않는다.
  · 미적용 버튼은 색 + 라벨 끝 `•` 두 가지로 표시된다.
  · 알림(toast)은 **모달이 아니다** — headless에서 멈추면 안 된다.
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


@pytest.fixture
def win(qapp, appdata):
    from etreport import demo
    from etreport.config.catalog import Catalog
    from etreport.config.settings import Settings
    from etreport.model.state import AppState
    from etreport.ui.mainwindow import MainWindow

    state = AppState()
    demo.load_demo(state)
    w = MainWindow(Settings(), Catalog(), state)
    w.show()
    qapp.processEvents()
    yield w
    w.close()


# ── 단축키 ───────────────────────────────────────────────────────────────
def test_f5_goes_to_the_visible_workspace(qapp, win):
    """같은 F5가 데이터 화면에서는 추출, 분석 화면에서는 적용이 된다.

    두 화면에 각각 걸어 두어도 숨은 위젯의 단축키는 Qt가 끄기 때문에 충돌하지
    않는다 — 이 전제가 깨지면 F5가 아무 데도 가지 않는다.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    fired: list[str] = []
    win.anal_ws.apply_config = lambda: fired.append("analysis")
    win.data_ws._run = lambda: fired.append("data")

    win._switch(1)
    qapp.processEvents()
    QTest.keyClick(win, Qt.Key_F5)
    qapp.processEvents()
    assert fired == ["analysis"]

    win._switch(0)
    qapp.processEvents()
    QTest.keyClick(win, Qt.Key_F5)
    qapp.processEvents()
    assert fired == ["analysis", "data"]


def test_ctrl_enter_runs_the_current_tab(qapp, win):
    """Ctrl+Enter는 보고 있는 탭의 주 버튼(stale_button_attr)을 누른다."""
    win._switch(1)
    for idx, attr in enumerate(("btn_draw", "btn_build", "btn_draw")):
        win.anal_ws.tabs.setCurrentIndex(idx)
        qapp.processEvents()
        tab = win.anal_ws.tabs.currentWidget()
        assert tab.stale_button_attr == attr
        pressed: list[int] = []
        getattr(tab, attr).clicked.connect(
            lambda _=False, hit=pressed, i=idx: hit.append(i))
        win.anal_ws._run_current_tab()
        qapp.processEvents()
        assert pressed, f"{idx}번 탭에서 주 동작이 실행되지 않았다"


def test_window_shortcuts_exist(win):
    """화면 전환·설명서는 창 전역이다."""
    from PySide6.QtGui import QShortcut

    keys = {s.key().toString() for s in win.findChildren(QShortcut)}
    assert {"Ctrl+1", "Ctrl+2", "F1"} <= keys


# ── 상태 레일 ────────────────────────────────────────────────────────────
def test_status_rail_lamp_tracks_state(qapp, win):
    """램프는 ○(미연결) → ◐(미적용) → ●(적용됨)."""
    st = win.state
    st.applied = False
    win._refresh_rail()
    assert win.rail_lamp.text() == "◐"            # 데모 데이터가 있으므로 미적용
    assert win.rail_lamp.property("state") == "dirty"

    st.applied = True
    win._refresh_rail()
    assert win.rail_lamp.text() == "●"
    assert win.rail_lamp.property("state") == "ok"

    st.data, st.applied = None, False
    win._refresh_rail()
    assert win.rail_lamp.text() == "○"


def test_status_rail_shows_counts_and_note(qapp, win):
    st = win.state
    st.excluded.add(next(iter(st.data["key"])))
    st.status_note = "추출 중   2/5"
    win._refresh_rail()
    text = win.rail_text.text()
    assert "item" in text and "포인트" in text
    assert f"제외 {len(st.excluded)}" in text     # 데모가 미리 찍어 둔 것 포함
    assert "추출 중" in text


def test_native_menubar_is_hidden_but_actions_remain(win):
    """메뉴는 상단바 버튼으로 올라가고 네이티브 메뉴바는 감춘다."""
    from PySide6.QtWidgets import QToolButton

    assert not win.menuBar().isVisible()
    assert [a.text() for a in win.menuBar().actions()] == ["템플릿", "도구",
                                                          "도움말"]
    titles = {b.text() for b in win.findChildren(QToolButton)
              if b.objectName() == "menuButton"}
    assert titles == {"템플릿", "도구", "도움말"}


def test_tools_menu_holds_the_file_utilities(qapp, win):
    """지금 보는 분석을 바꾸지 않는 도구는 상단바로 갔다(설계 §2 이동표).

    계측·fab tracking은 **여기 없다** — 프레임에 컬럼을 붙여 그릴 수 있는 것이
    달라지므로 소스 레일 [추가 소스]다.
    """
    act = next(a for a in win.menuBar().actions() if a.text() == "도구")
    texts = [x.text() for x in act.menu().actions() if x.text()]
    assert any("S3" in t for t in texts)
    assert any("SQL" in t for t in texts)
    assert any("캐시" in t for t in texts)
    assert not any("계측" in t or "tracking" in t for t in texts)
    # 캐시 항목은 열 때 크기를 센다
    assert "캐시" in win._refresh_cache_action()


# ── 미적용 표시 ──────────────────────────────────────────────────────────
def test_dirty_marks_label_not_only_color(qapp):
    """색만으로 알리지 않는다 — 라벨 끝에 •가 붙고, 풀면 사라진다."""
    from PySide6.QtWidgets import QPushButton

    from etreport.ui.tabs.common import set_dirty

    b = QPushButton("적용")
    set_dirty(b, True)
    assert b.text().endswith("•")
    assert b.property("dirty") == "true"
    set_dirty(b, True)                       # 두 번 걸어도 •는 하나만
    assert b.text().count("•") == 1
    set_dirty(b, False)
    assert b.text() == "적용"


# ── 도크 파일 행 ─────────────────────────────────────────────────────────
def test_file_rows_use_two_columns(qapp, win):
    """공백 문자로 맞추지 않고 라벨/값을 나눈다. 비었으면 '고르기'."""
    ws = win.anal_ws
    ws._refresh_dock()
    val = ws._file_values["db"]
    assert val.text() == "고르기"
    assert val.property("empty") == "true"

    ws.cfg().db_path = "/tmp/et_data.duckdb"
    ws._refresh_dock()
    assert "et_data.duckdb" in ws._file_values["db"].text()
    assert ws._file_values["db"].property("empty") == "false"


def test_extra_sources_section_starts_collapsed(qapp, win):
    """추가 소스(계측·tracking)는 접어 둔다(펼침 여부는 설정에 남는다)."""
    ws = win.anal_ws
    assert ws.sources_section.toggle.isChecked()        # 접힘
    ws.sources_section.toggle.setChecked(False)
    assert ws.settings.dock_tools_open is True


# ── 알림 ─────────────────────────────────────────────────────────────────
def test_toast_is_not_modal(qapp, win):
    """알림은 모달이 아니어야 한다 — 모달이면 headless에서 영영 멈춘다."""
    from PySide6.QtCore import Qt

    from etreport.ui.widgets.toast import Toast, toast

    t = toast(win.anal_ws, "Excel 캐시 3개를 비웠습니다")
    qapp.processEvents()
    assert isinstance(t, Toast)
    assert t.isVisible()
    assert not t.isModal()
    # 클릭을 가로채지 않는다 — 알림 뒤의 버튼을 그대로 누를 수 있어야 한다
    assert t.testAttribute(Qt.WA_TransparentForMouseEvents)


def test_apply_warnings_are_not_modal(qapp, win, no_modal_dialogs, monkeypatch):
    """[적용]에서 건너뛴 행은 모달로 띄우지 않는다 — 레일 버튼으로 연다."""
    from etreport.model.session import LoadReport
    from etreport.ui.widgets import table_dialog

    ws = win.anal_ws
    rep = LoadReport(lines=["리포메터 OK"],
                     warnings=["[리포메터] 3행 Vt: ITEMID가 비어 있습니다",
                               "[plot] 5행: x가 ALIAS가 아닙니다"],
                     notes=["lot PA2 결손"])
    ws._apply_done(ws.cfg(), rep)
    qapp.processEvents()
    assert no_modal_dialogs == []
    assert ws.btn_apply_log.isVisible()

    shown = []
    monkeypatch.setattr(table_dialog.FrameDialog, "exec",
                        lambda self: shown.append(self))
    ws._open_apply_log()
    df = shown[0].df
    # 마지막 열(내용)이 남는 폭을 채운다 — 오른쪽이 비면 안 된다
    assert shown[0].table.horizontalHeader().stretchLastSection()
    assert df.columns == ["구분", "출처", "내용"]
    assert df.row(0) == ("제외", "리포메터", "3행 Vt: ITEMID가 비어 있습니다")
    assert df["구분"].to_list() == ["제외", "제외", "확인"]

    ws._apply_done(ws.cfg(), LoadReport(lines=["OK"]))
    assert not ws.btn_apply_log.isVisible()                 # 알릴 것이 없으면 숨는다


def test_toast_skips_when_parent_is_hidden(qapp):
    """창이 없거나 숨어 있으면 조용히 건너뛴다(종료 중 알림으로 죽지 않게)."""
    from PySide6.QtWidgets import QWidget

    from etreport.ui.widgets.toast import toast

    w = QWidget()                                    # show() 하지 않는다
    assert toast(w, "무시된다") is None


# ── 데이터 화면 ──────────────────────────────────────────────────────────
def test_run_button_says_only_the_action(qapp, win):
    """진행 단계는 라벨·레일이 말한다 — 버튼 글자는 바뀌지 않는다."""
    ws = win.data_ws
    before = ws.btn_run.text()
    ws._on_step("추출 중", 2, 5)
    assert ws.btn_run.text() == before
    assert "추출 중" in ws.lbl_step.text()
    assert "2/5" in ws.lbl_step.text()
    assert "추출 중" in win.state.status_note        # 다른 화면에서도 보이게


def test_data_layout_switches_to_two_columns(qapp, win):
    """넓으면 2열, 좁으면 1열."""
    ws = win.data_ws
    ws._relayout(2)
    assert ws.grid.itemAtPosition(0, 1) is not None   # 조회 조건이 오른쪽 열
    ws._relayout(1)
    assert ws.grid.itemAtPosition(0, 1) is None


# ── 도크가 제 폭 안에 들어간다 ───────────────────────────────────────────
def test_rail_content_fits_its_fixed_width(qapp, win):
    """레일 안의 어떤 위젯도 레일 폭 밖으로 나가지 않는다.

    레일은 고정 폭 스크롤 영역이고 가로 스크롤이 없다. 그래서 위젯 하나가
    폭을 넘기면 **오류 없이 오른쪽이 잘린 채로** 남는다 — 예전에 항목이 긴
    콤보(`측정 조건별 (step · 온도)`) 하나가 최소 폭을 338px로 밀어 올려
    도크 전체의 오른쪽 글자가 통째로 사라졌고, 아무도 눈치채지 못했다.
    폭이 300 → 244로 좁아졌으므로 더 빠듯하다.
    """
    from PySide6.QtWidgets import QScrollArea, QWidget

    from etreport.ui.source_rail import RAIL_WIDTH

    rail = win.anal_ws.rail
    panel = rail.findChild(QWidget, "dock")
    scroll = rail.findChild(QScrollArea, "dockScroll")
    assert panel is not None and scroll is not None
    assert rail.width() == RAIL_WIDTH
    assert panel.minimumSizeHint().width() <= scroll.viewport().width(), (
        f"레일 내용이 {panel.minimumSizeHint().width()}px를 요구하는데 "
        f"쓸 수 있는 폭은 {scroll.viewport().width()}px다 — 오른쪽이 잘린다")
    # 하단 고정 블록([적용]·요약)도 같은 폭 안에 들어가야 한다
    foot = rail.findChild(QWidget, "railFooter")
    assert foot.minimumSizeHint().width() <= RAIL_WIDTH


def test_dock_checkbox_text_uses_chrome_colour(qapp):
    """도크의 체크박스 글자는 크롬 글자색이어야 한다.

    QCheckBox는 QLabel이 아니라 `#dock QLabel` 규칙이 닿지 않는다. 규칙이
    없으면 전역 `QWidget { color:TEXT }`(측정면용 먹색)를 물려받아 어두운
    도크 위에 어두운 글자가 찍히고, 라벨이 통째로 안 보인다.
    """
    from etreport.ui import theme

    css = theme.qss_path().read_text(encoding="utf-8")
    assert "#dock QCheckBox" in css
