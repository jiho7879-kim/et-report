"""UI 스모크 — 데모 데이터로 창을 실제로 조립해 본다.

화면 로직에는 테스트가 없었다. 여기서는 headless(offscreen)로 창을 띄워
① 세 탭이 조립되는지 ② 지연 계산(StaleMixin) 규약이 지켜지는지
③ 요약 복사가 화면 표와 같은 숫자인지 를 확인한다.

Qt를 띄울 수 없는 환경이면 통째로 skip한다.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture(autouse=True)
def _no_modal_dialogs(qapp, no_modal_dialogs):
    """모달 차단은 conftest의 공용 픽스처가 한다 — 여기서는 자동으로 걸기만."""
    return no_modal_dialogs


@pytest.fixture
def win(qapp, appdata):
    from etreport import demo
    from etreport.config.catalog import Catalog
    from etreport.config.settings import Settings
    from etreport.model.state import AppState
    from etreport.ui.mainwindow import MainWindow

    state = AppState()
    demo.load_demo(state)
    w = MainWindow(Settings.defaults(), Catalog(), state)
    yield w
    w.close()


def test_window_builds_all_tabs(win):
    assert win.anal_ws.tabs.count() == 3
    assert [win.anal_ws.tabs.tabText(i) for i in range(3)] == [
        "탐색", "요약", "리포트 구성"]


def test_explore_draws_demo_data(win):
    tab = win.anal_ws.tab_explore
    tab.redraw()
    assert not tab._stale
    assert tab.canvas.figure.axes, "그림이 그려지지 않았다"
    assert "포인트" in tab.lbl_info.text()


def test_stale_toggles_dirty_property(win):
    tab = win.anal_ws.tab_summary
    tab.rebuild()
    assert tab.btn_build.property("dirty") == "false"
    tab.mark_stale()
    assert tab.btn_build.property("dirty") == "true"
    assert "표 만들기" in tab.lbl_state.text()
    # 요약은 보고 있어도 자동으로 만들지 않는다 (확정 사양)
    assert tab._stale


def test_summary_table_and_copy_agree(win, monkeypatch):
    """화면 표 · 클립보드 TSV · xlsx가 같은 숫자를 써야 한다 (Δ 포함)."""
    from etreport.export.excel import build_table, to_tsv

    tab = win.anal_ws.tab_summary
    tab.chk_delta.setChecked(True)                     # Δ vs REF 켜기
    tab.rebuild()

    # 화면 표에서 첫 데이터 행이 있는 카드를 고르고, **그 카드의 CAT1**으로
    # 같은 표를 다시 만들어 비교한다 — 카드마다 라벨 열 수가 다를 수 있다(§14).
    from PySide6.QtWidgets import QTableWidget
    cards = [t for t in tab.host.findChildren(QTableWidget)
             if t.objectName() == "sumTable"]
    names = win.state.report.table_names()
    idx = next(i for i, t in enumerate(cards) if t.item(0, 0) is not None)
    table, cat1 = cards[idx], names[idx]
    td = build_table(win.state, cat1, tab._options())
    tsv = to_tsv(td, tab._options()).splitlines()

    # 라벨 열 수는 CAT 개수·규격 열(§14)에 따라 달라진다 — 헤더에서 읽는다
    n_lab = len(td.labels())
    screen_first = [table.item(0, c).text()
                    for c in range(n_lab, table.columnCount())]
    tsv_first = tsv[2].split("\t")[n_lab:]
    # 화면은 제외 개수를 '값  −N'으로 덧붙이므로 값 부분만 비교한다
    assert [s.split("  ")[0] for s in screen_first] == tsv_first


def test_copy_puts_same_text_on_clipboard(win):
    from PySide6.QtWidgets import QApplication

    tab = win.anal_ws.tab_summary
    tab.rebuild()
    cat1 = win.state.report.table_names()[0]
    tab._copy(cat1)
    text = QApplication.clipboard().text()
    assert text.startswith("\t\t\t")                   # 헤더 3칸 비움
    assert cat1 in text or "제외" in text              # 캡션이 붙는다


def test_report_exclusion_redraws_without_rebuilding_widgets(win):
    """제외 클릭마다 캔버스 6개를 새로 만들던 문제 — 같은 위젯을 재사용해야."""
    tab = win.anal_ws.tab_report
    tab.rebuild()
    if not tab._canvases:
        pytest.skip("데모 리포트에 plot 슬롯이 없다")
    before = dict(tab._canvases)

    tab.show()                                         # 보고 있는 상태로
    tab._on_exclusion()
    assert tab._canvases == before                     # 같은 객체 그대로


def test_report_ppt_button_needs_data(win, monkeypatch):
    """데이터가 없으면 워커를 띄우지 않고 안내만 한다."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    tab = win.anal_ws.tab_report
    win.state.data = None
    seen: list = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: seen.append(a[1:])))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("/tmp/should_not.pptx", "")))
    tab._ppt()
    assert seen and "PPT" in seen[0][0]
    assert not getattr(tab, "_bg_workers", [])         # 워커를 띄우지 않았다


def test_close_shuts_down_cleanly(win):
    """종료 시 추출 스레드·DB 연결 정리 경로가 예외 없이 돈다."""
    win.close()
    assert win.state.store is None                     # 연결을 닫고 비운다
    assert not win.data_ws._th.isRunning() if hasattr(win.data_ws, "_th") else True
