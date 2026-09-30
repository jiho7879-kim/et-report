"""Regression coverage for hollow markers, experiment axes and rich summary copy."""
import polars as pl

from etreport.export.excel import SummaryOptions, to_html, to_tsv
from etreport.model import categories
from etreport.model.split import SYMBOL_CHOICES, SYMBOL_LABELS, SYMBOLS, parse_split_text
from etreport.render.pptgen import TableData


def test_split_conditions_are_categories_and_keep_measurements():
    split = parse_split_text("lot\twafer\tDose\tI\n l1 \t1\t10\twrong\nL1\t2\t20\twrong")
    data = pl.DataFrame({"lot": ["L1", "L1", "L2"], "wafer": ["W01", "02", "1"],
                         "I": [1.0, 2.0, 3.0], "gid": ["", "", ""]})
    result = split.attach_conditions(data)
    assert result["Dose"].to_list() == ["10", "20", None]
    assert result["I"].to_list() == [1.0, 2.0, 3.0]
    assert "Dose" in categories.choices(result)
    assert categories.is_category("Dose", result)
    assert result.height == data.height
    updated = parse_split_text("lot\twafer\tDose\nL1\t1\t30")
    assert updated.attach_conditions(result)["Dose"].to_list() == ["30", None, None]


def test_split_conditions_reattach_without_factors():
    from etreport.data.loader import reattach_sources
    from etreport.model.state import AppState

    st = AppState()
    st.split = parse_split_text("lot\twafer\tRecipe\nL\t1\tPOR")
    data = pl.DataFrame({"lot": ["L"], "wafer": ["W01"]})
    assert reattach_sources(data, st)["Recipe"].to_list() == ["POR"]


def test_hollow_symbols_are_opt_in():
    assert SYMBOLS == ["o", "s", "t", "d", "+"]
    assert len(SYMBOL_CHOICES) == len(SYMBOL_LABELS)
    assert SYMBOL_CHOICES[-4:] == ["o-open", "s-open", "t-open", "d-open"]


def test_html_preserves_merges_colors_and_escapes_labels():
    rows = [{"cats": ["<A>", "same"], "item": item, "values": [1.2, None],
             "offspec": [True, False]} for item in ["I&1", "I2"]]
    rows.append({"cats": ["B", "same"], "item": "I3", "values": [2.0, 3.0],
                 "offspec": [False, False]})
    td = TableData("Table", [("L&1", ["01", "02"])], rows)
    opt = SummaryOptions(session_caption="caption <safe>")
    html = to_html(td, opt)
    assert 'colspan="2"' in html
    assert html.count('&lt;A&gt;') == 1
    assert html.count('>same</td>') == 2  # lower category merge stops at parent boundary
    assert 'background-color:#ffecee;color:#d70015' in html
    assert 'L&amp;1' in html and 'I&amp;1' in html
    assert 'caption &lt;safe&gt;' in html
    assert "1.20\t" in to_tsv(td, opt)


def test_summary_copy_sets_html_and_plain_text(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from etreport import demo
    from etreport.model.state import AppState, StateBus
    from etreport.ui.tabs.summary import SummaryTab

    app = QApplication.instance() or QApplication([])
    st = AppState()
    demo.load_demo(st)
    tab = SummaryTab(st, StateBus())
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    tab._copy(st.report.table_names()[0])
    mime = app.clipboard().mimeData()
    assert mime.hasHtml() and mime.hasText()
    assert '<table' in mime.html()
    assert '\t' in mime.text()
    app.clipboard().clear()
    tab.deleteLater()
