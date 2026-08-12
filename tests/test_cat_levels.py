"""§3.3 — CAT 계층은 **개수를 고정하지 않는다**.

확정 사양: "CAT4, CAT5… 원하는 만큼 늘릴 수 있어야 함. 개수 고정 금지 —
정규식으로 `CAT\\d+`를 찾아 번호순 정렬". 예전에는 CAT1~CAT3만 읽고 CAT4는
버렸으며, 반대로 **CAT3 열이 없는 템플릿은 '컬럼 누락'으로 아예 중단**됐다.

검증 체크리스트(§14)의 "CAT4·CAT5까지 있는 템플릿이 화면·xlsx·PPT에서 모두
렌더링되는지"에 대응한다 — xlsx는 Excel이 필요하므로 복사(TSV)와 공통 헬퍼로
확인한다(같은 `labels()`/`label_values()`를 쓴다).
"""
from __future__ import annotations

import os

import polars as pl
import pytest

from etreport.model import templates as T
from etreport.model.specs import TableRowSpec
from etreport.render import pptgen
from etreport.render.pptgen import TableData

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PLOT_ROW = {"page": 1, "x": "A", "y": "B", "order": 1, "title1": "P1",
            "title2": "t", "Report": "R1", "Type": "scatter",
            "x_name": "", "y_name": ""}


def _templates(table_rows: list[dict], plot_rows=None) -> T.Templates:
    """검증을 거치지 않고 build_report만 태운다 (시트 파싱은 test_templates)."""
    t = T.Templates()
    t.plot_rows = T._with_rowno(pl.DataFrame(plot_rows or [PLOT_ROW]))
    t.table_rows = T._with_rowno(pl.DataFrame(table_rows))
    return t


def _row(item: str, *cats: str, report: str = "R1") -> dict:
    return {"item_id": item, "Report": report,
            **{f"CAT{i + 1}": c for i, c in enumerate(cats)}}


# ── 열 찾기 ──────────────────────────────────────────────────
def test_cat_columns_are_found_in_number_order():
    df = pl.DataFrame([{"item_id": "a", "CAT3": "c", "CAT10": "j", "CAT1": "x",
                        "CAT2": "b", "Report": "R"}])
    assert T.cat_columns(df) == ["CAT1", "CAT2", "CAT3", "CAT10"]


def test_non_cat_columns_are_ignored():
    df = pl.DataFrame([{"item_id": "a", "CATEGORY": "x", "CAT1": "y",
                        "CATALOG": "z", "Report": "R"}])
    assert T.cat_columns(df) == ["CAT1"]


# ── 로딩 ─────────────────────────────────────────────────────
def test_five_levels_survive_build_report():
    """★ CAT5까지 그대로 실린다 (예전에는 CAT3까지만 읽었다)."""
    t = _templates([_row("A", "DC", "NMOS", "SVT", "W1", "L1")])

    spec = T.build_report(t, "R1")

    assert spec.cat_names == ["CAT2", "CAT3", "CAT4", "CAT5"]
    r = spec.table_rows[0]
    assert r.cats == ["DC", "NMOS", "SVT", "W1", "L1"]
    assert r.cat1 == "DC"                       # 표를 나누는 기준
    assert r.subcats == ["NMOS", "SVT", "W1", "L1"]


def test_two_levels_are_fine_too():
    """★ CAT3이 없어도 중단하지 않는다 — 예전에는 '컬럼 누락: CAT3'였다."""
    t = _templates([_row("A", "DC", "NMOS")])
    T._validate(t, _RF)

    assert not t.errors
    spec = T.build_report(t, "R1")
    assert spec.table_rows[0].cats == ["DC", "NMOS"]
    assert spec.cat_names == ["CAT2"]


def test_missing_cat1_is_still_an_error():
    """CAT1은 표를 나누는 기준이라 없으면 구조 오류다."""
    t = _templates([{"item_id": "A", "CAT2": "NMOS", "Report": "R1"}])
    T._validate(t, _RF)

    assert any("CAT1" in e.message for e in t.errors)


class _RFStub:
    """검증에 필요한 것은 alias 목록뿐이다."""

    def __init__(self) -> None:
        self.by_alias = {"A": object(), "B": object()}


_RF = _RFStub()


# ── 표 데이터 헬퍼 (화면·복사·xlsx·PPT 공용) ────────────────
def test_labels_and_values_stay_aligned():
    td = TableData("DC", [("PA1", ["01"])], [
        {"cats": ["N", "SVT", "W1"], "item": "a", "values": [1.0],
         "offspec": [False]},
        {"cats": ["N"], "item": "b", "values": [2.0], "offspec": [False]},
    ], cat_names=["구분", "Vt", "W"])

    assert td.labels() == ["구분", "Vt", "W", "item"]
    assert td.label_values(td.rows[0]) == ["N", "SVT", "W1", "a"]
    assert td.label_values(td.rows[1]) == ["N", "", "", "b"]   # 빈 칸으로 채운다


def test_labels_fall_back_to_cat_numbers():
    td = TableData("DC", [("PA1", ["01"])],
                   [{"cats": ["N", "SVT"], "item": "a", "values": [1.0],
                     "offspec": [False]}])
    assert td.labels() == ["CAT2", "CAT3", "item"]


def test_copy_tsv_has_every_cat_column():
    from etreport.export.excel import to_tsv

    td = TableData("DC", [("PA1", ["01", "02"])], [
        {"cats": ["N", "SVT", "W1", "L1"], "item": "a", "values": [1.0, 2.0],
         "offspec": [False, False]}])

    lines = to_tsv(td).splitlines()
    assert lines[1].split("\t")[:5] == ["CAT2", "CAT3", "CAT4", "CAT5", "item"]
    assert lines[2].split("\t")[:5] == ["N", "SVT", "W1", "L1", "a"]
    assert lines[2].split("\t")[5:] == ["1.00", "2.00"]


# ── PPT ──────────────────────────────────────────────────────
def _deck(td: TableData):
    from etreport.data.reformatter import Reformatter
    from etreport.model.specs import ReportSpec
    return pptgen.build_deck(
        report=ReportSpec(report="R"), experiments=[""],
        group_styles_of=lambda exp: [], plot_data_of=lambda exp, spec: {},
        tables=[td], rf=Reformatter(), log_patterns=[],
        exclusion_log=pl.DataFrame({"key_hash": [], "reason": [],
                                    "created_at": []}))


def test_ppt_table_renders_all_cat_columns():
    """★ CAT4·CAT5가 PPT 표에도 열로 나온다."""
    td = TableData("DC", [("PA1", ["01", "02"])], [
        {"cats": ["N", "SVT", "W1", "L1"], "item": "a", "values": [1.0, 2.0],
         "offspec": [False, False]},
        {"cats": ["N", "SVT", "W1", "L2"], "item": "b", "values": [3.0, 4.0],
         "offspec": [False, False]},
        {"cats": ["P", "SVT", "W1", "L1"], "item": "c", "values": [5.0, 6.0],
         "offspec": [False, False]},
    ])

    tbl = next(sh.table for sh in _deck(td).slides[0].shapes if sh.has_table)

    assert len(tbl.columns) == 5 + 2                    # CAT2~5 + item + wafer 2
    assert [tbl.cell(1, c).text for c in range(5)] == [
        "CAT2", "CAT3", "CAT4", "CAT5", "item"]
    assert tbl.cell(2, 0).text == "N" and tbl.cell(2, 0).span_height == 2
    # 상위(CAT2)가 바뀌면 하위 병합도 끊긴다 — 3행이 하나로 묶이면 안 된다
    assert tbl.cell(2, 1).span_height == 2
    assert tbl.cell(4, 1).text == "SVT"
    assert tbl.cell(2, 4).text == "a" and tbl.cell(2, 4).span_height == 1


def test_ppt_label_width_follows_cat_count():
    narrow = pptgen.table_width_in(10, n_labels=3)[0]
    wide = pptgen.table_width_in(10, n_labels=6)[0]
    assert wide > narrow


# ── 화면(Summary 탭) ─────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def test_summary_tab_shows_every_cat_column(qapp, appdata):
    """★ 화면 표에도 CAT4·CAT5 열이 나온다."""
    from PySide6.QtWidgets import QTableWidget

    from etreport import demo
    from etreport.model.state import AppState, StateBus
    from etreport.ui.tabs.summary import SummaryTab

    st = AppState()
    demo.load_demo(st)
    alias = st.report.table_rows[0].item_id
    st.report.table_rows = [TableRowSpec(alias, ["DC", "N", "SVT", "W1", "L1"])]
    st.report.cat_names = ["CAT2", "CAT3", "CAT4", "CAT5"]

    tab = SummaryTab(st, StateBus())
    tab.rebuild()

    table = tab.findChild(QTableWidget)
    heads = [table.horizontalHeaderItem(i).text()
             for i in range(table.columnCount())]
    assert heads[:5] == ["CAT2", "CAT3", "CAT4", "CAT5", "item"]
    assert [table.item(0, i).text() for i in range(5)] == [
        "N", "SVT", "W1", "L1", alias]
