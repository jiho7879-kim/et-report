"""ADDP 수식 엔진 — 행 단위 엔진(`compile_formula`)의 의미론.

여기가 리포메터 값의 기준이다. 벡터 경로(`_compile_expr`)는 이 결과와 같아야
하고, 그 동치성은 test_reformatter_vector.py에서 따로 검증한다.
"""
from __future__ import annotations

import math

import pytest

from etreport.data.reformatter import FormulaError, compile_formula


def ev(src: str, **env):
    return compile_formula(src)(env)


def test_refs_are_collected_in_order():
    fn = compile_formula("{B}/{A}+{B}")
    assert fn.refs == ["B", "A", "B"]


def test_arithmetic():
    assert ev("{A}+{B}*2", A=1.0, B=2.0) == 5.0
    assert ev("({A}+{B})/2", A=1.0, B=3.0) == 2.0
    assert ev("-{A}", A=1.5) == -1.5
    assert ev("{A}**2", A=3.0) == 9.0


def test_null_propagates():
    assert ev("{A}+{B}", A=None, B=2.0) is None
    assert ev("{A}*2", A=None) is None
    assert ev("{A}+{B}", A=1.0, B=None) is None


def test_missing_ref_is_null():
    assert ev("{A}+{B}", A=1.0) is None          # B가 env에 아예 없음


def test_zero_division_is_null():
    assert ev("{A}/{B}", A=1.0, B=0.0) is None
    assert ev("{A}/({B}-{C})", A=1.0, B=2.0, C=2.0) is None


# ── 함수 ─────────────────────────────────────────────────────
def test_std_is_sample_stdev_skipping_null():
    """엑셀 STDEV와 동치 — 표본(n-1), NULL 제외, 유효값<2면 NULL."""
    assert ev("Std({A},{B})", A=1.0, B=3.0) == pytest.approx(math.sqrt(2))
    assert ev("Std({A},{B},{C})", A=2.0, B=4.0, C=4.0) == pytest.approx(
        math.sqrt(((2 - 10 / 3) ** 2 + 2 * (4 - 10 / 3) ** 2) / 2))
    # NULL은 빼고 남은 2개로 계산
    assert ev("Std({A},{B},{C})", A=1.0, B=None, C=3.0) == pytest.approx(
        math.sqrt(2))
    assert ev("Std({A},{B})", A=1.0, B=None) is None      # 유효값 1개
    assert ev("Std({A},{B})", A=None, B=None) is None


def test_aggregate_functions_skip_null():
    assert ev("Avg({A},{B},{C})", A=1.0, B=None, C=3.0) == 2.0
    assert ev("Sum({A},{B})", A=1.0, B=None) == 1.0
    assert ev("Min({A},{B})", A=None, B=-2.0) == -2.0
    assert ev("Max({A},{B})", A=None, B=-2.0) == -2.0
    assert ev("Avg({A},{B})", A=None, B=None) is None


def test_domain_guards():
    assert ev("Sqrt({A})", A=4.0) == 2.0
    assert ev("Sqrt({A})", A=-1.0) is None
    assert ev("Log10({A})", A=100.0) == 2.0
    assert ev("Log10({A})", A=0.0) is None
    assert ev("Ln({A})", A=math.e) == pytest.approx(1.0)
    assert ev("Ln({A})", A=-1.0) is None
    assert ev("Abs({A})", A=-3.0) == 3.0
    assert ev("Abs({A})", A=None) is None


@pytest.mark.parametrize("name", ["Abs", "ABS", "abs"])
def test_function_names_are_case_insensitive(name):
    assert ev(f"{name}({{A}})", A=-2.0) == 2.0


# ── 안전성 (eval을 직접 쓰지 않는다) ─────────────────────────
@pytest.mark.parametrize("src", [
    "__import__('os')",
    "open('/etc/passwd')",
    "{A}.__class__.__mro__",
    "[x for x in (1,2)]",
    "lambda: 1",
    "{A} if {A} else 1",
    "{A} and {B}",
    "{A} > 0",
    "print({A})",
])
def test_unsafe_syntax_rejected(src):
    with pytest.raises(FormulaError):
        compile_formula(src)


def test_bare_alias_without_braces_is_rejected():
    """엑셀에서 {}를 빠뜨리는 실수 — 통과시키면 계산 시점에 NameError로 터진다."""
    with pytest.raises(FormulaError, match="중괄호"):
        compile_formula("IT0001-IT0002")
    with pytest.raises(FormulaError, match="중괄호"):
        compile_formula("{A}-IT0002")


def test_builtins_are_not_reachable():
    with pytest.raises(FormulaError):
        compile_formula("len({A})")


def test_syntax_error_message_is_readable():
    with pytest.raises(FormulaError, match="수식 구문 오류"):
        compile_formula("{A} +")
