"""왼쪽 소스 레일 — 설계 §1 규칙 1·§2 이동표·§5 필요 표시.

여기서 고정하는 것:
  · 레일에는 **데이터셋을 정하는 것**만 있다(표현은 인스펙터).
  · 파일 5행이 `●필수 / ○선택`을 말한다 — 예전엔 5행이 전부 똑같았다.
  · 저장·새 이름·삭제는 `⋯` 메뉴 하나로 접힌다(primary 색을 먹지 않는다).
  · `[적용]`은 **스크롤 밖**에 있다 — 목록을 내려도 사라지지 않는다.
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
def ws(qapp, tmp_path_factory):
    """분석 화면 하나를 모듈 안에서 돌려 쓴다(창 조립이 이 파일에서 가장 비싸다)."""
    from _pytest.monkeypatch import MonkeyPatch

    from etreport import demo
    from etreport.config.settings import Settings
    from etreport.model.state import AppState, StateBus
    from etreport.ui.analysis_ws import AnalysisWorkspace

    mp = MonkeyPatch()
    mp.setenv("APPDATA", str(tmp_path_factory.mktemp("appdata")))
    state = AppState()
    demo.load_demo(state)
    w = AnalysisWorkspace(state, StateBus(), Settings.defaults())
    w.resize(1366, 700)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    mp.undo()


# ── 파일 5행 ─────────────────────────────────────────────────────────────
def test_file_rows_mark_required_and_optional(ws):
    """무엇이 없으면 [적용]이 실패하는지 화면이 먼저 말한다(설계 §0 G).

    필수 여부는 레일이 아니라 `ui/guidance`가 정한다 — [적용]의 실제 검증과
    어긋나면 "표시는 초록인데 적용은 실패"가 된다. Table이 필수인 이유:
    `session.apply_config`는 plot·table이 **둘 다** 있을 때만 템플릿을 읽는다.
    """
    from etreport.ui import guidance
    from etreport.ui.source_rail import FILES

    reqs = {r.key: r for r in guidance.analysis_requirements(ws.state, ws.cfg())}
    assert {k: not reqs[k].optional for k, _label in FILES} == {
        "db": True, "plot": True, "tbl": True, "rfm": True, "split": False}

    ws._refresh_dock()
    for key, _label in FILES:
        row = getattr(ws.rail, f"btn_{key}")
        need = not reqs[key].optional
        assert row.property("need") == ("true" if need else "false")
        dot = ws.rail._file_dots[key]
        # 색만으로 알리지 않는다 — 모양이 다르고 툴팁이 붙는다
        assert dot.text() == ("●" if need else "○")
        assert dot.toolTip()


def test_file_row_shows_the_pick_prompt_when_empty(ws):
    ws.rail.set_file("db", "")
    val = ws.rail._file_values["db"]
    assert val.text() == "고르기"
    assert val.property("empty") == "true"
    ws.rail.set_file("db", "et_data.duckdb", "et_data")
    assert val.property("empty") == "false"
    # 좁은 레일에서는 **앞을 줄인다** — 끝의 이름·시트가 중요하다. 전체 경로는 툴팁.
    assert val.text().endswith("[et_data]")
    assert val.toolTip() == "et_data.duckdb  [et_data]"


# ── 프리셋 ⋯ ─────────────────────────────────────────────────────────────
def test_preset_actions_live_in_one_menu(ws):
    """저장·새 이름·삭제 3버튼이 최상단을 먹지 않는다(설계 §2 이동표)."""
    from PySide6.QtWidgets import QPushButton

    texts = [a.text() for a in ws.rail.btn_cfg_menu.menu().actions()]
    assert texts == ["저장", "새 이름으로 저장…", "삭제…"]
    labels = {b.text() for b in ws.rail.findChildren(QPushButton)}
    assert "저장" not in labels and "새 이름" not in labels and "삭제" not in labels


# ── 자리 규칙 ────────────────────────────────────────────────────────────
def test_apply_button_is_outside_the_scroll(ws):
    """스크롤을 끝까지 내려도 [적용]은 그 자리에 있다(설계 §1 규칙 3의 레일 판)."""
    from PySide6.QtWidgets import QScrollArea

    scroll = ws.rail.findChild(QScrollArea, "dockScroll")
    assert scroll is not None
    assert ws.btn_apply.parent() is not scroll.widget()
    bar = scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    ws.rail.repaint()
    assert ws.btn_apply.isVisible()


def test_presentation_controls_moved_to_the_inspector(ws):
    """로그 패턴·lot 심볼은 데이터를 바꾸지 않는다 → 오른쪽(설계 §1 규칙 1)."""
    assert ws.ed_log.parent() is not None
    assert not ws.rail.isAncestorOf(ws.ed_log)
    assert not ws.rail.isAncestorOf(ws.chk_lot_split)
    assert ws.inspector.isAncestorOf(ws.ed_log)
    assert ws.inspector.isAncestorOf(ws.chk_lot_split)


def test_extra_sources_are_on_the_rail(ws):
    """계측·tracking은 프레임에 컬럼을 붙인다 → 데이터 쪽이므로 왼쪽."""
    from PySide6.QtWidgets import QPushButton

    texts = [b.text() for b in ws.rail.sources_section.findChildren(QPushButton)]
    assert any("계측" in t for t in texts)
    assert any("tracking" in t for t in texts)


def test_outlier_section_title_carries_its_state(ws):
    """접혀 있어도 상태가 제목에 적힌다.

    제목이 말하는 것은 **지금 데이터에 걸려 있는 것**이다(체크박스가 아니라
    `state.tukey`). 체크만 하고 [적용]을 누르지 않았을 때 제목이 먼저 바뀌면
    화면의 숫자와 어긋난다 — 지연 계산 규약.
    """
    from etreport.model.outliers import TukeyConfig

    ws.state.tukey = TukeyConfig(False)
    ws._refresh_dock()
    assert "꺼짐" in ws.tukey_section.toggle.text()

    ws.state.tukey = TukeyConfig(True, 3.0)
    ws._refresh_dock()
    title = ws.tukey_section.toggle.text()
    assert "꺼짐" not in title and "3" in title
    ws.state.tukey = TukeyConfig(False)
    ws._refresh_dock()


def test_footer_summary_counts_without_a_second_big_number(ws):
    """제외 개수의 큰 숫자는 상단 상태 레일이 갖는다 — 여기는 요약 한 줄."""
    from PySide6.QtWidgets import QLabel

    ws._refresh_dock()
    assert "그룹" in ws.lbl_summary.text()
    big = [w for w in ws.rail.findChildren(QLabel)
           if w.objectName() == "bigNum"]
    assert big == []
