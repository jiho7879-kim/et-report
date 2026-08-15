"""§5.1 REPORT 콤보 없음 · §11.4 템플릿 예시·도움말 · §3.4 붙여넣기 · §14 배정 lot.

한 세션에서 함께 고친 네 가지를 묶어 둔다 — 전부 "화면에 무엇이 보이는가"라
UI를 실제로 조립해 확인해야 한다(offscreen).
"""
from __future__ import annotations

import os

import polars as pl
import pytest

from etreport.model import wafers

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


# ── §5.1 REPORT 콤보를 두지 않는다 ───────────────────────────
def test_no_report_combo_in_the_dock(win):
    """★ 확정 사양 — 리포트는 고르는 게 아니라 템플릿이 정한다."""
    assert not hasattr(win.anal_ws, "rep_combo")
    assert hasattr(win.anal_ws, "lbl_report")


def test_report_name_is_shown_as_text(win):
    win.anal_ws.state.reports = ["M2_ET"]
    win.anal_ws._show_report("M2_ET")

    assert "M2_ET" in win.anal_ws.lbl_report.text()
    assert "리포트" not in win.anal_ws.lbl_report.text()   # 여럿일 때만 안내


def test_multiple_reports_are_only_announced(win):
    win.anal_ws.state.reports = ["M2_ET", "DEV_EVAL", "AC"]
    win.anal_ws._show_report("M2_ET")

    text = win.anal_ws.lbl_report.text()
    assert "M2_ET" in text
    assert "리포트 3개" in text and "DEV_EVAL" in text
    assert "Report 열" in text                    # 바꾸는 방법을 알려 준다


# ── §11.4 템플릿 예시 ────────────────────────────────────────
def test_four_samples_with_descriptions():
    from etreport.export import templates_sample as ts

    samples = ts.all_samples()
    assert [s.key for s in samples] == [
        "reformatter", "plot_template", "table_template", "split"]
    for s in samples:
        assert not s.data.is_empty()
        assert set(s.desc.columns) == {"컬럼", "의미", "예시", "규칙"}
        assert s.desc.height >= 5


def test_sample_columns_match_the_real_loaders():
    """예시가 실제 로더가 요구하는 컬럼을 갖춰야 쓸모가 있다."""
    from etreport.data.reformatter import COLUMNS as RF_COLS
    from etreport.export import templates_sample as ts
    from etreport.model.templates import PLOT_COLS, TBL_REQUIRED

    got = {s.key: s.data.columns for s in ts.all_samples()}
    assert set(RF_COLS) <= set(got["reformatter"])
    assert set(PLOT_COLS) <= set(got["plot_template"])
    assert set(TBL_REQUIRED) <= set(got["table_template"])
    assert {"lot", "wafer"} <= set(got["split"])


def test_sample_reformatter_actually_loads(fake_sheet):
    """예시 리포메터가 검증을 통과한다 — 오류 없이 ADDP까지 계산 가능해야 한다."""
    from etreport.data.reformatter import load
    from etreport.export import templates_sample as ts

    fake_sheet(ts.reformatter_sample().data)
    rf = load("sample.xlsx")

    assert not rf.errors and not rf.warnings
    assert [r.alias for r in rf.addps()] == ["Idsat N/P", "Vt spread"]
    assert rf.by_alias["Ioff N"].absolute                # ABSOLUTE=TRUE 인식


def test_sample_split_and_table_are_usable():
    from etreport.export import templates_sample as ts
    from etreport.model.split import SplitMatrix

    sm = SplitMatrix.from_dataframe(ts.split_sample().data)
    assert sm.steps == ["M1", "M5"]
    assert sm.confounds(["M1"]) == []                    # 예시는 혼입이 없다

    # table 예시는 CAT4까지 있다 — 개수 고정 금지의 산 증거(§3.3)
    from etreport.model.templates import cat_columns
    assert cat_columns(ts.table_sample().data) == ["CAT1", "CAT2", "CAT3", "CAT4"]


def test_csv_fallback_when_excel_is_missing(tmp_path):
    """★ Excel이 없으면 CSV + 설명 CSV로 떨어진다(리눅스에서 실제로 이 경로)."""
    from etreport.export import templates_sample as ts

    made = ts.save_all(tmp_path)

    assert len(made) == 4
    for p in made:
        assert p.suffix == ".csv"                        # 이 환경엔 xlwings 없음
        assert p.read_bytes()[:3] == b"\xef\xbb\xbf"     # 엑셀 한글 BOM
        assert (p.parent / f"{p.stem}_설명.csv").exists()


def test_single_file_bundle_falls_back_to_csv(tmp_path):
    """Excel이 있으면 4종을 한 파일에 시트로, 없으면 종류별 CSV로 떨어진다."""
    from etreport.export import templates_sample as ts

    made = ts.save_all(tmp_path, single_file=True)

    assert len(made) == 4                      # 이 환경엔 xlwings가 없다
    assert {p.suffix for p in made} == {".csv"}


def test_menus_exist(win):
    titles = [m.title() for m in win.menuBar().findChildren(type(win.menuBar()))]
    actions = [a.text() for m in win.menuBar().actions()
               if m.menu() for a in m.menu().actions()]
    assert [a.text() for a in win.menuBar().actions()] == ["템플릿", "도움말"]
    assert any("리포메터" in a for a in actions)
    assert any("4종 한 파일로" in a for a in actions)
    assert any("관계도" in a for a in actions)
    assert titles is not None


def test_help_text_covers_the_four_files():
    from etreport.ui.mainwindow import HELP_TEXT

    for word in ("리포메터", "plot 템플릿", "table 템플릿", "실험 조건",
                 "ALIAS", "Report"):
        assert word in HELP_TEXT


# ── §3.4 붙여넣기 ────────────────────────────────────────────
def test_pasted_text_parses_like_a_file():
    from etreport.model.split import parse_split_text

    sm = parse_split_text("lot\twafer\tM1\tM5\n"
                          "PA123\t01\tBase\tBase\n"
                          "PA123\t02\tHi\tBase\n")

    assert sm.steps == ["M1", "M5"]
    assert sm.wide.height == 2
    assert sm.assignment(["M1"])[wafers.key("PA123", "02")] != \
        sm.assignment(["M1"])[wafers.key("PA123", "01")]


def test_pasted_text_accepts_commas_and_short_rows():
    from etreport.model.split import parse_split_text

    sm = parse_split_text("lot,wafer,M1\nPA1,01,Base\nPA1,02\n")

    assert sm.steps == ["M1"]
    assert sm.wide["M1"].to_list() == ["Base", "Base"]   # 빈칸은 baseline


@pytest.mark.parametrize("text", ["", "lot\twafer\tM1", "머리글만\n한줄"])
def test_bad_paste_raises_instead_of_guessing(text):
    from etreport.model.split import parse_split_text

    with pytest.raises(ValueError):
        parse_split_text(text)


def test_split_source_dialog_parses_while_typing(qapp, appdata):
    """★ 붙여넣으면 즉시 파싱해 미리보기와 step 목록을 보여 준다."""
    from etreport.ui.widgets.split_dialog import SplitSourceDialog

    dlg = SplitSourceDialog()
    assert dlg.matrix is None                     # 아직 아무것도 없다

    dlg.paste.setPlainText("lot\twafer\tM1\nPA1\t01\tBase\nPA1\t02\tHi")

    assert dlg.matrix is not None
    assert dlg.matrix.steps == ["M1"]
    assert dlg.text and not dlg.path              # 출처는 '붙여넣기'
    assert dlg.preview.rowCount() == 2
    assert "step 1개: M1" in dlg.lbl_info.text()
    dlg.deleteLater()


def test_split_config_keeps_pasted_text():
    """붙여넣은 조건이 프리셋에 남아 [적용] 때 다시 읽힌다."""
    from etreport.config.settings import AnalysisConfig
    from etreport.model.session import apply_config
    from etreport.model.state import AppState

    cfg = AnalysisConfig(name="t", split_text="lot,wafer,M1\nPA1,01,Base\n"
                                              "PA1,02,Hi")
    st = AppState()
    rep = apply_config(st, cfg)

    assert rep.ok and st.split is not None
    assert st.split.steps == ["M1"]


# ── §14 표에는 배정된 lot만 ──────────────────────────────────
def test_table_shows_only_assigned_lots():
    """★ 그룹 배정 후에는 배정된 wafer만 표 열로 나온다."""
    from etreport.model.state import AppState

    st = AppState()
    st.data = pl.DataFrame({
        "key": ["1", "2", "3", "4"],
        "lot": ["PA1", "PA1", "PB2", "PB2"],
        "wafer": ["01", "02", "01", "02"],
        "gid": ["g0", "", "", ""],
        "Vt": [0.4, 0.5, 0.6, 0.7]})

    assert st.wafer_columns() == [("PA1", ["01"])]


def test_all_lots_when_nothing_is_assigned():
    """그룹을 안 쓰는 흐름 — 배정이 하나도 없으면 전체를 보여 준다."""
    from etreport.model.state import AppState

    st = AppState()
    st.data = pl.DataFrame({
        "key": ["1", "2"], "lot": ["PA1", "PB2"], "wafer": ["01", "01"],
        "gid": ["", ""], "Vt": [0.4, 0.5]})

    assert st.wafer_columns() == [("PA1", ["01"]), ("PB2", ["01"])]
