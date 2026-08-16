"""csv·tsv도 정식 입력이다 — Excel 없는 PC에서 리포메터·템플릿을 읽는 길.

예시 파일 생성(§11.4)은 Excel이 없으면 CSV로 떨어뜨리는데, 정작 그 CSV를 다시
읽을 방법이 없었다(읽기는 xlwings 전용). 데모 번들도 같은 문제를 밟는다.
그래서 `xlio`가 csv·tsv를 직접 읽되 **열 타입 규칙은 엑셀 경로와 똑같이** 준다.
"""
from __future__ import annotations

import polars as pl
import pytest

from etreport.data import xlio


@pytest.fixture
def csv_file(tmp_path, appdata):
    p = tmp_path / "리포메터.csv"
    p.write_text(
        "CATEGORY,ITEMID,ALIAS,ABSOLUTE,SCALE FACTOR,ADDP FORM,UNIT,"
        "SPECLOW,SPECHIGH,TARGET,CODE\n"
        "REAL,ET_A,A,TRUE,1e6,,uA,0.3,0.9,0.6,0012\n"
        "REAL,ET_B,B,N,1.0,,V,,,,7\n"
        "ADDP,,C,N,1.0,{A}/{B},,,,,\n", encoding="utf-8")
    return p


def test_text_table_is_read_without_excel(csv_file):
    df = xlio.read_sheet(str(csv_file))

    assert df.columns[:5] == ["CATEGORY", "ITEMID", "ALIAS", "ABSOLUTE",
                              "SCALE FACTOR"]
    assert df.height == 3
    assert df["SCALE FACTOR"].dtype == pl.Float64      # 전부 숫자 → Float64
    assert df["ALIAS"].dtype == pl.Utf8
    assert df["SCALE FACTOR"][0] == pytest.approx(1e6)


def test_blank_cells_become_null_not_empty_text(csv_file):
    df = xlio.read_sheet(str(csv_file))

    assert df["ADDP FORM"][0] is None                  # 빈 칸은 NULL
    assert df["ADDP FORM"][2] == "{A}/{B}"
    assert df["SPECLOW"][1] is None


def test_zero_padded_codes_survive(csv_file):
    """`0012`를 숫자로 보면 `12`가 된다 — 코드 열이 뭉개지면 안 된다."""
    df = xlio.read_sheet(str(csv_file))

    assert df["CODE"].to_list() == ["0012", "7", None]


def test_sheet_argument_is_ignored_and_names_are_single(csv_file):
    assert xlio.sheet_names(str(csv_file)) == [xlio.TEXT_SHEET]
    a, b = xlio.read_sheets(str(csv_file), [0, "아무거나"])
    assert a.equals(b)


def test_reformatter_loads_from_csv(csv_file):
    from etreport.data import reformatter as R

    rf = R.load(str(csv_file))

    assert not rf.errors
    assert [r.alias for r in rf.rules] == ["A", "B", "C"]
    assert rf.by_alias["A"].scale == pytest.approx(1e6)
    assert rf.by_alias["A"].absolute and not rf.by_alias["B"].absolute
    assert rf.by_alias["A"].speclow == pytest.approx(0.3)
    assert rf.by_alias["C"].formula == "{A}/{B}"


def test_write_sheet_round_trips_csv(csv_file):
    df = xlio.read_sheet(str(csv_file))
    changed = df.with_columns(pl.lit("REAL").alias("CATEGORY"))

    bak = xlio.write_sheet(str(csv_file), 0, changed)

    assert bak.endswith(".bak")
    again = xlio.read_sheet(str(csv_file))
    assert set(again["CATEGORY"]) == {"REAL"}


def test_tsv_uses_tab_separator(tmp_path, appdata):
    p = tmp_path / "표.tsv"
    p.write_text("A\tB\n1\t가\n", encoding="utf-8")

    df = xlio.read_sheet(str(p))

    assert df.columns == ["A", "B"]
    assert df["B"][0] == "가"


def test_xlsx_still_needs_excel(tmp_path):
    """xlsx 경로는 그대로 xlwings — csv 지원이 그 규칙을 흔들지 않는다."""
    assert not xlio.is_text_table("a.xlsx")
    assert xlio.is_text_table("a.csv") and xlio.is_text_table("a.TSV")
