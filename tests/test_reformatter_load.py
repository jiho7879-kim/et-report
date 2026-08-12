"""리포메터 시트 읽기·검증 — `data/reformatter.load()` + `validate()`.

엑셀 스키마가 곧 계약이다. 여기서 깨지면 사내 PC에서 리포메터가 통째로
안 뜨거나, 더 나쁘게는 조용히 item이 사라진다.
"""
from __future__ import annotations

import polars as pl
import pytest

from etreport.data import reformatter as R
from tests.factory import make_rules, rules_frame, sheet_from_rows

HDR = R.COLUMNS


def sheet(*rows: list) -> pl.DataFrame:
    return sheet_from_rows(HDR, list(rows))


def real(itemid, alias, absolute="N", scale=1.0, low=None, high=None):
    return ["REAL", itemid, alias, absolute, scale, None, "V", low, high, None]


def addp(alias, form, absolute="N"):
    return ["ADDP", None, alias, absolute, 1.0, form, "", None, None, None]


# ── 정상 파싱 ────────────────────────────────────────────────
def test_load_parses_sheet(fake_sheet):
    seen = fake_sheet(sheet(
        real("ET_A", "A", scale=1000.0, low=-1.5, high=2.5),
        real("ET_B", "B", absolute="Y"),
        addp("D", "{A}-{B}"),
    ), expect_path="R.xlsx")

    rf = R.load("R.xlsx", "리포메터")

    assert seen == [("R.xlsx", "리포메터", False)]      # Excel은 한 번만 읽는다
    assert not rf.errors and not rf.warnings
    assert [r.alias for r in rf.rules] == ["A", "B", "D"]
    a, b, d = rf.rules
    assert (a.scale, a.speclow, a.spechigh, a.absolute) == (1000.0, -1.5, 2.5, False)
    assert b.absolute is True
    assert d.category == "ADDP" and d.formula == "{A}-{B}"
    assert d.row == 4                                   # 헤더가 1행 → 3번째 데이터=4행
    assert [r.alias for r in rf.reals()] == ["A", "B"]
    assert [r.alias for r in rf.addps()] == ["D"]


def test_scale_and_spec_accept_text_cells(fake_sheet):
    """엑셀에서 숫자가 텍스트로 들어와도 읽어야 한다(자주 있는 사고)."""
    fake_sheet(sheet(
        ["REAL", "ET_A", "A", "y", "0.001", None, "V", " -1.5 ", "2.5", "0.5"],
    ))
    (a,) = R.load("x.xlsx").rules
    assert a.scale == 0.001
    assert (a.speclow, a.spechigh, a.target) == (-1.5, 2.5, 0.5)
    assert a.absolute is True                            # 'y' 소문자도 인정


def test_blank_and_missing_values(fake_sheet):
    fake_sheet(sheet(
        ["REAL", "ET_A", "A", None, None, None, None, None, None, None],
        ["REAL", "ET_B", "  ", "N", 1.0, None, None, None, None, None],   # ALIAS 없음
    ))
    rf = R.load("x.xlsx")
    assert [r.alias for r in rf.rules] == ["A"]          # ALIAS 없는 행은 그냥 무시
    a = rf.rules[0]
    assert (a.scale, a.absolute, a.speclow) == (1.0, False, None)


def test_missing_column_is_fatal(fake_sheet):
    fake_sheet(pl.DataFrame({"CATEGORY": ["REAL"], "ITEMID": ["ET_A"]}))
    rf = R.load("x.xlsx")
    assert rf.errors and "컬럼 누락" in rf.errors[0].message
    assert "ALIAS" in rf.errors[0].message
    assert rf.rules == []


def test_rf_load_uses_sheet_argument(fake_sheet):
    seen = fake_sheet(sheet(real("ET_A", "A")))
    R.load("x.xlsx", 2)
    assert seen[0][1] == 2


# ── 헤더 정규화(띄어쓰기·밑줄·대소문자 무시) ──────────────────
def test_header_variants_treated_as_canonical(fake_sheet):
    """'ADDP FORM'/'ADDPFORM'/'SCALE FACTOR'/'SCALE_FACTOR' 등으로 적어도
    같은 컬럼으로 본다."""
    hdr_var = ["CATEGORY", "ITEMID", "ALIAS", "ABSOLUTE", "SCALE_FACTOR",
               "ADDPFORM", "UNIT", "SPECLOW", "SPECHIGH", "TARGET"]
    fake_sheet(sheet_from_rows(hdr_var, [
        ["REAL", "ET_A", "A", "N", "0.001", None, "V", None, None, None],
        ["ADDP", None, "D", "N", 1.0, "{A}*2", "", None, None, None],
    ]))
    rf = R.load("x.xlsx")
    assert not rf.errors
    a, d = rf.rules
    assert a.scale == 0.001
    assert d.formula == "{A}*2"


def test_header_variants_case_and_space_insensitive(fake_sheet):
    """소문자+공백 조합('scale factor'/'addp form')도 표준과 같이 본다."""
    hdr_var = ["category", "itemid", "alias", "absolute", "scale factor",
               "addp form", "unit", "speclow", "spechigh", "target"]
    fake_sheet(sheet_from_rows(hdr_var, [
        ["REAL", "ET_A", "A", "N", "1000", None, "V", None, None, None],
        ["ADDP", None, "D", "N", 1.0, "{A}+1", "", None, None, None],
    ]))
    rf = R.load("x.xlsx")
    assert not rf.errors
    a, d = rf.rules
    assert a.scale == 1000.0
    assert d.formula == "{A}+1"


def test_exact_header_preferred_over_variant(fake_sheet):
    """표준 헤더('ADDP FORM')와 변형('ADDPFORM')이 함께 있어도
    표준 쪽을 우선한다(중복 변형 헤더로 덮어쓰지 않는다)."""
    hdr_both = [*HDR, "ADDPFORM"]
    fake_sheet(sheet_from_rows(hdr_both, [
        ["REAL", "ET_A", "A", "N", 1.0, None, "V", None, None, None, "X"],
    ]))
    rf = R.load("x.xlsx")
    assert not rf.errors
    assert len(rf.rules) == 1


# ── 옵션 기하 컬럼 WIDTH/LENGTH ────────────────────────────
HDR_GEOM = [*HDR, "WIDTH", "LENGTH"]


def test_load_parses_optional_geom_columns(fake_sheet):
    """WIDTH/LENGTH 컬럼이 있는 시트 → 모든 Rule에 w/l 파싱 (문자열 숫자·빈 셀 포함)."""
    fake_sheet(sheet_from_rows(HDR_GEOM, [
        ["REAL", "ET_A", "A", "N", 1.0, None, "V", None, None, None,
         "0.34", 12.5],                       # W는 문자열 숫자(Utf8 열)
        ["REAL", "ET_B", "B", "N", 1.0, None, "V", None, None, None,
         None, None],                         # 빈 셀 → None
    ]))
    a, b = R.load("x.xlsx").rules
    assert (a.w, a.l) == (0.34, 12.5)         # '0.34'도 숫자로 파싱
    assert (b.w, b.l) == (None, None)
    assert (a.row, b.row) == (2, 3)           # 행 번호는 w/l과 무관하게 그대로


def test_load_without_geom_columns_keeps_legacy(fake_sheet):
    """WIDTH/LENGTH 컬럼이 없는 시트(구계약) → 모든 Rule의 w/l은 None (무회귀)."""
    fake_sheet(sheet(real("ET_A", "A"), real("ET_B", "B")))
    rf = R.load("x.xlsx")
    assert not rf.errors
    assert all(r.w is None and r.l is None for r in rf.rules)


# ── 검증 규칙 ────────────────────────────────────────────────
def test_duplicate_alias_last_wins(fake_sheet):
    fake_sheet(sheet(
        real("ET_A1", "A", scale=1.0),
        real("ET_A2", "A", scale=999.0),
    ))
    rf = R.load("x.xlsx")
    assert len(rf.rules) == 1
    assert rf.rules[0].itemid == "ET_A2"                 # 아래 행이 이긴다
    assert len(rf.warnings) == 1
    assert "중복" in rf.warnings[0].message and "3행" in rf.warnings[0].message


def test_addp_may_reference_rows_above(fake_sheet):
    fake_sheet(sheet(
        real("ET_A", "A"), real("ET_B", "B"),
        addp("D1", "{A}+{B}"),
        addp("D2", "{D1}*2"),                            # ADDP-on-ADDP
    ))
    rf = R.load("x.xlsx")
    assert not rf.warnings
    assert [r.alias for r in rf.addps()] == ["D1", "D2"]


def test_forward_reference_is_dropped(fake_sheet):
    """아래 행 참조 = 순환참조 차단 규칙 위반 → 그 item만 제외."""
    fake_sheet(sheet(
        real("ET_A", "A"),
        addp("D", "{A}+{LATER}"),
        real("ET_L", "LATER"),
    ))
    rf = R.load("x.xlsx")
    assert [r.alias for r in rf.rules] == ["A", "LATER"]
    assert len(rf.warnings) == 1
    assert "참조 불가" in rf.warnings[0].message
    assert "LATER" in rf.warnings[0].message


def test_dropped_addp_cascades(fake_sheet):
    """제외된 ADDP를 참조하는 아래 ADDP도 연쇄로 빠진다."""
    fake_sheet(sheet(
        real("ET_A", "A"),
        addp("BAD", "{NOPE}*2"),
        addp("CHILD", "{BAD}+1"),
        addp("OK", "{A}+1"),
    ))
    rf = R.load("x.xlsx")
    assert [r.alias for r in rf.rules] == ["A", "OK"]
    assert {w.alias for w in rf.warnings} == {"BAD", "CHILD"}


def test_empty_formula_is_dropped(fake_sheet):
    fake_sheet(sheet(real("ET_A", "A"), addp("D", "")))
    rf = R.load("x.xlsx")
    assert [r.alias for r in rf.rules] == ["A"]
    assert "ADDP FORM이 비어" in rf.warnings[0].message


@pytest.mark.parametrize("formula", [
    "{A} + ",                       # 구문 오류
    "__import__('os').system('x')",  # 임의 실행
    "{A}.__class__",                # 속성 접근
    "Foo({A})",                     # 화이트리스트 밖 함수
    "{A} if {A} else 0",            # 삼항
    "[{A}]",                        # 리스트
    "{A} > 1",                      # 비교
    "ET_A-{A}",                     # 중괄호를 빠뜨린 ALIAS
])
def test_unsafe_or_broken_formula_is_dropped(fake_sheet, formula):
    fake_sheet(sheet(real("ET_A", "A"), addp("D", formula)))
    rf = R.load("x.xlsx")
    assert [r.alias for r in rf.rules] == ["A"], f"{formula} 가 통과했다"
    assert rf.warnings and "제외" in rf.warnings[0].message


def test_report_lines_lists_row_and_alias(fake_sheet):
    fake_sheet(sheet(real("ET_A", "A"), addp("D", "{NOPE}")))
    rf = R.load("x.xlsx")
    (line,) = rf.report_lines()
    assert line.startswith("3행 D:")


# ── 대규모 시트 (실측 규모) ──────────────────────────────────
def test_thousand_item_sheet(fake_sheet):
    rules = make_rules(n_real=1000, n_addp=40, seed=3)
    fake_sheet(rules_frame(rules))
    rf = R.load("big.xlsx")
    assert not rf.errors and not rf.warnings
    assert len(rf.reals()) == 1000
    assert len(rf.addps()) == 40
    assert len(rf.by_alias) == 1040
