"""입력 가이드 — 설계 §5.

여기서 고정하는 것:
  · 필수 목록이 **[적용]의 실제 검증과 일치**한다(가장 중요한 계약 —
    "표시는 초록인데 적용은 실패"가 가장 나쁜 결과다).
  · 채우면 표시가 사라진다.
  · 빈 상태 화면과 가이드 모드가 **같은 판정**을 읽는다.
  · 가이드 모드는 다 채우면 스스로 꺼진다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from etreport.ui import guidance


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
def ws(qapp, appdata):
    from etreport.config.settings import Settings
    from etreport.model.state import AppState, StateBus
    from etreport.ui.analysis_ws import AnalysisWorkspace

    w = AnalysisWorkspace(AppState(), StateBus(), Settings.defaults())
    yield w
    w.deleteLater()


# ── 필수 판정이 [적용]과 같은가 ──────────────────────────────────────────
def test_required_set_matches_apply_config():
    """`session.apply_config`가 실제로 요구하는 것과 같아야 한다.

    템플릿은 **plot·table이 둘 다 있을 때만** 읽는다 — 그래서 Table도 필수다.
    실험 조건은 없어도 되고, 나머지 셋이 없으면 볼 것이 없다.
    """
    from etreport.config.settings import AnalysisConfig
    from etreport.model.state import AppState

    reqs = guidance.analysis_requirements(AppState(), AnalysisConfig(name="t"))
    need = {r.key for r in reqs if not r.optional}
    assert need == {"db", "plot", "tbl", "rfm", "apply"}
    assert {r.key for r in reqs if r.optional} == {"split", "group"}
    # 전부 한 줄짜리 설명이 붙는다 — 강조만 하고 말이 없으면 소용이 없다
    assert all(r.how and "\n" not in r.how for r in reqs)


def test_requirements_clear_as_you_fill_them():
    from etreport.config.settings import AnalysisConfig
    from etreport.model.state import AppState

    st, cfg = AppState(), AnalysisConfig(name="t")
    assert guidance.next_step(guidance.analysis_requirements(st, cfg)).key == "db"

    cfg.db_path = "/tmp/x.duckdb"
    assert guidance.next_step(
        guidance.analysis_requirements(st, cfg)).key == "plot"
    cfg.plot_template_path = "/tmp/p.xlsx"
    assert guidance.next_step(
        guidance.analysis_requirements(st, cfg)).key == "tbl"
    cfg.table_template_path = "/tmp/t.xlsx"
    cfg.reformatter_path = "/tmp/r.xlsx"
    assert guidance.next_step(
        guidance.analysis_requirements(st, cfg)).key == "apply"

    import polars as pl
    st.data = pl.DataFrame({"key": ["k"]})
    st.applied = True
    assert guidance.next_step(guidance.analysis_requirements(st, cfg)) is None


def test_data_screen_requirements_are_ordered():
    from etreport.config.settings import ExtractPreset

    reqs = guidance.data_requirements(ExtractPreset(name="t"))
    assert [r.key for r in reqs] == ["db", "rfm", "line", "period"]
    assert guidance.next_step(reqs) is not None


# ── 화면 반영 ────────────────────────────────────────────────────────────
def test_empty_rows_are_marked_and_cleared(ws):
    """비어 있는 필수 입력에 표시가 붙고, 채우면 사라진다."""
    ws._refresh_dock()
    row = ws.rail.btn_db
    assert row.property("needs") == "true"
    lab, _text = ws.rail._file_labels["db"]
    assert lab.text().endswith("*"), "색만으로 알리지 않는다 — 라벨의 *"
    assert row.toolTip(), "무엇을 해야 하는지 툴팁에 있어야 한다"

    ws.cfg().db_path = "/tmp/x.duckdb"
    ws._refresh_dock()
    assert row.property("needs") == "false"
    assert not lab.text().endswith("*")


def test_optional_rows_are_never_marked(ws):
    ws._refresh_dock()
    assert ws.rail.btn_split.property("need") == "false"
    assert ws.rail.btn_split.property("needs") == "false"


def test_guide_mode_points_at_one_place_and_turns_itself_off(ws):
    ws._refresh_dock()
    assert ws.toggle_guide() is True
    from etreport.ui.source_rail import FILES
    marked = [k for k, _l in FILES
              if getattr(ws.rail, f"btn_{k}").property("guide") == "true"]
    assert len(marked) == 1, "가이드는 **한 곳만** 짚는다"
    assert ws.lbl_apply.text().strip(), "무엇을 해야 하는지 한 줄로 말한다"

    # 다 채우면 스스로 꺼진다
    import polars as pl
    c = ws.cfg()
    c.db_path = "/tmp/x.duckdb"
    c.plot_template_path = c.table_template_path = c.reformatter_path = "/tmp/a"
    ws.state.data = pl.DataFrame({"key": ["k"]})
    ws.state.applied = True
    ws._refresh_dock()
    assert ws._guide is False
    assert all(getattr(ws.rail, f"btn_{k}").property("guide") == "false"
               for k in ("db", "plot", "tbl", "rfm", "split"))


def test_guide_does_not_turn_on_when_nothing_is_missing(ws):
    import polars as pl

    c = ws.cfg()
    c.db_path = "/tmp/x.duckdb"
    c.plot_template_path = c.table_template_path = c.reformatter_path = "/tmp/a"
    ws.state.data = pl.DataFrame({"key": ["k"]})
    ws.state.applied = True
    ws._refresh_dock()
    assert ws.toggle_guide() is False


# ── 빈 상태 ──────────────────────────────────────────────────────────────
def test_empty_messages_tell_what_to_press():
    from etreport.model.state import AppState

    st = AppState()
    for what in ("plot", "table", "report"):
        msg = guidance.empty_message(st, what)
        assert "적용" in msg or "DB" in msg
        assert msg.strip()


def test_qss_draws_the_marks():
    """표시는 QSS 규칙 하나라 같은 속성을 단 위젯이면 어느 창에서든 붙는다."""
    from etreport.ui import theme

    css = theme.qss_path().read_text(encoding="utf-8")
    assert '[needs="true"]' in css
    assert '[guide="true"]' in css
