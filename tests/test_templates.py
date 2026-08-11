"""plot/table 템플릿 로딩·검증 — 리포메터 ALIAS와의 계약.

리포메터에서 계산되지 않는 ALIAS를 템플릿이 참조하면 그 행만 조용히 빠지고
경고가 남아야 한다(중단하지 않는다). 이 규칙이 무너지면 리포트에서 plot이
통째로 사라지거나 반대로 빈 plot이 들어간다.
"""
from __future__ import annotations

import polars as pl
import pytest

from etreport.model import templates as T
from tests.factory import make_reformatter

PLOT_HDR = T.PLOT_COLS
TBL_HDR = T.TBL_COLS


@pytest.fixture
def rf():
    return make_reformatter(n_real=5, n_addp=2, seed=0)   # IT0000~IT0004, ADDP00·01


def plot_sheet(rows):
    return pl.DataFrame(dict(zip(PLOT_HDR, map(list, zip(*rows)))))


def tbl_sheet(rows):
    return pl.DataFrame(dict(zip(TBL_HDR, map(list, zip(*rows)))))


def prow(page, x, y, order, t1="P", t2="", report="R1", typ="scatter",
         xn="", yn=""):
    return [page, x, y, order, t1, t2, report, typ, xn, yn]


def load(fake_sheet, plot_rows, tbl_rows, rf, monkeypatch):
    """plot·table 시트를 각각 주입해 templates.load()를 태운다."""
    import etreport.data.xlio as xlio
    frames = {"plot.xlsx": plot_sheet(plot_rows), "tbl.xlsx": tbl_sheet(tbl_rows)}
    monkeypatch.setattr(xlio, "read_sheet",
                        lambda path, sheet=0, force=False: frames[path])
    monkeypatch.setattr(xlio, "read_sheets",
                        lambda path, sheets, force=False:
                        [frames[path] for _ in sheets])
    return T.load("plot.xlsx", "tbl.xlsx", rf)


def test_valid_template(fake_sheet, rf, monkeypatch):
    t = load(fake_sheet,
             [prow(1, "IT0000", "IT0001", 1), prow(1, "IT0002", "ADDP00", 2)],
             [["IT0000", "DC", "NMOS", "W1", "R1"]], rf, monkeypatch)
    assert not t.errors and not t.warnings
    assert t.reports() == ["R1"]

    spec = T.build_report(t, "R1")
    assert len(spec.pages) == 1
    page = spec.pages[0]
    assert page.number == 1 and page.title == "P"
    assert page.slots[0].x == "IT0000" and page.slots[1].y == "ADDP00"
    assert page.slots[2] is None
    assert spec.table_names() == ["DC"]
    assert spec.table_rows[0].item_id == "IT0000"


def test_missing_column_is_fatal(fake_sheet, rf, monkeypatch):
    import etreport.data.xlio as xlio
    bad = pl.DataFrame({"page": [1], "x": ["IT0000"]})
    monkeypatch.setattr(xlio, "read_sheet", lambda p, s=0, force=False: bad)
    monkeypatch.setattr(xlio, "read_sheets",
                        lambda p, s, force=False: [bad for _ in s])
    t = T.load("a.xlsx", "b.xlsx", rf)
    assert t.errors and "컬럼 누락" in t.errors[0].message


def test_unknown_alias_row_is_skipped(fake_sheet, rf, monkeypatch):
    t = load(fake_sheet,
             [prow(1, "IT0000", "NOPE", 1), prow(1, "IT0001", "IT0002", 2)],
             [["NOPE", "DC", "", "", "R1"], ["IT0001", "DC", "", "", "R1"]],
             rf, monkeypatch)
    assert not t.errors
    assert len(t.warnings) == 2
    assert all("계산 불가" in w.message for w in t.warnings)

    spec = T.build_report(t, "R1")
    assert spec.pages[0].slots[0] is None          # 건너뛴 plot
    assert spec.pages[0].slots[1] is not None
    assert [r.item_id for r in spec.table_rows] == ["IT0001"]


def test_order_out_of_range_is_skipped(fake_sheet, rf, monkeypatch):
    t = load(fake_sheet,
             [prow(1, "IT0000", "IT0001", 0), prow(1, "IT0000", "IT0001", 7),
              prow(1, "IT0000", "IT0001", 6)],
             [["IT0000", "DC", "", "", "R1"]], rf, monkeypatch)
    assert len(t.warnings) == 2
    assert all("order" in w.message for w in t.warnings)
    assert T.build_report(t, "R1").pages[0].slots[5] is not None


def test_comma_pairs_must_match(fake_sheet, rf, monkeypatch):
    t = load(fake_sheet,
             [prow(1, "IT0000,IT0001", "IT0002,IT0003", 1),   # 2:2 OK
              prow(1, "IT0000", "IT0001,IT0002", 2),          # 1:2 OK (브로드캐스트)
              prow(1, "IT0000,IT0001,IT0002", "IT0003,IT0004", 3)],  # 3:2 오류
             [["IT0000", "DC", "", "", "R1"]], rf, monkeypatch)
    assert len(t.warnings) == 1
    assert "쉼표" in t.warnings[0].message
    spec = T.build_report(t, "R1")
    assert spec.pages[0].slots[2] is None
    assert spec.pages[0].slots[0].pairs() == [("IT0000", "IT0002"),
                                              ("IT0001", "IT0003")]
    assert spec.pages[0].slots[1].pairs() == [("IT0000", "IT0001"),
                                              ("IT0000", "IT0002")]


def test_duplicate_slot_warns_and_last_wins(fake_sheet, rf, monkeypatch):
    t = load(fake_sheet,
             [prow(1, "IT0000", "IT0001", 1, t2="first"),
              prow(1, "IT0002", "IT0003", 1, t2="second")],
             [["IT0000", "DC", "", "", "R1"]], rf, monkeypatch)
    assert any("중복" in w.message for w in t.warnings)
    assert T.build_report(t, "R1").pages[0].slots[0].title == "second"


def test_reports_are_separated(fake_sheet, rf, monkeypatch):
    t = load(fake_sheet,
             [prow(1, "IT0000", "IT0001", 1, report="R1"),
              prow(1, "IT0002", "IT0003", 1, report="R2")],
             [["IT0000", "DC", "", "", "R1"],
              ["IT0001", "AC", "", "", "R2"]], rf, monkeypatch)
    assert t.reports() == ["R1", "R2"]
    r1, r2 = T.build_report(t, "R1"), T.build_report(t, "R2")
    assert r1.pages[0].slots[0].x == "IT0000"
    assert r2.pages[0].slots[0].x == "IT0002"
    assert r1.table_names() == ["DC"] and r2.table_names() == ["AC"]


def test_table_type_row_skips_alias_check(fake_sheet, rf, monkeypatch):
    """Type=table 슬롯은 x/y가 ALIAS가 아니어도 된다(표를 꽂는 자리)."""
    t = load(fake_sheet,
             [prow(1, "", "", 1, typ="table", t2="DC")],
             [["IT0000", "DC", "", "", "R1"]], rf, monkeypatch)
    assert not t.warnings
    assert T.build_report(t, "R1").pages[0].slots[0].type == "table"


def test_empty_cat1_row_is_skipped(fake_sheet, rf, monkeypatch):
    t = load(fake_sheet, [prow(1, "IT0000", "IT0001", 1)],
             [["IT0000", "", "", "", "R1"]], rf, monkeypatch)
    assert any("CAT1" in w.message for w in t.warnings)
    assert T.build_report(t, "R1").table_rows == []
