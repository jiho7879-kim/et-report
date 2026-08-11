"""벡터 경로 ≡ 행 단위 경로.

`apply()`는 수식을 polars 식으로 번역해(`_compile_expr`) 한 번에 계산하고,
번역이 안 되는 것만 행 단위 엔진(`compile_formula`)으로 떨어뜨린다. 두 경로가
갈라지면 "어제 값과 다르다"는 형태로만 드러나기 때문에 여기서 못 잡으면
현장에서 잡을 방법이 없다.

NULL(미측정) · 0(분모) · 음수(정의역)를 넉넉히 섞은 프레임에서 값이 완전히
같아야 한다.
"""
from __future__ import annotations

import polars as pl
import pytest

from etreport.data.reformatter import _compile_expr, compile_formula
from tests.factory import ADDP_SHAPES, fill, make_wide

ALIASES = ["A", "B", "C", "D"]

FORMULAS = [fill(s, *ALIASES) for s in ADDP_SHAPES] + [
    "{A}",
    "{A}+1",
    "-{A}",
    "{A}**2",
    "{A}/{B}",
    "{A}/({B}-{C})",              # 분모가 0이 되는 행이 반드시 생긴다
    "Std({A},{B})",
    "Sum({A},{B})",               # 전부 NULL인 행 → NULL이어야 한다 (0 아님)
    "Avg({A},{B})",
    "Min({A},{B})",
    "Max({A},{B})",
    "Sqrt({A})",                  # 음수 → NULL
    "Log10({A})",                 # 0·음수 → NULL
    "Ln({A})",
    "Abs({A})",
    "Std({A},{B},{C},{D})/Avg({A},{B},{C},{D})",
]


def _row_engine(src: str, wide: pl.DataFrame) -> list[float | None]:
    fn = compile_formula(src)
    return [fn(r) for r in wide.iter_rows(named=True)]


def _vector(src: str, wide: pl.DataFrame) -> list[float | None]:
    expr = _compile_expr(src, set(wide.columns))
    assert expr is not None, f"벡터 번역에 실패했다 — 행 단위로 떨어진다: {src}"
    return wide.select(expr.cast(pl.Float64).alias("v"))["v"].to_list()


def assert_same(vec, rows, src, wide):
    assert len(vec) == len(rows)
    for i, (v, r) in enumerate(zip(vec, rows)):
        ctx = f"{src} · {i}행 {wide.row(i, named=True)}"
        if r is None:
            assert v is None, f"벡터={v} / 행={r}  ({ctx})"
        else:
            assert v is not None, f"벡터=NULL / 행={r}  ({ctx})"
            assert v == pytest.approx(r, rel=1e-9, abs=1e-12), ctx


@pytest.mark.parametrize("src", FORMULAS)
def test_vector_matches_row_engine(src):
    wide = make_wide(ALIASES, 2000, seed=7, null_rate=0.25, zero_rate=0.08)
    assert_same(_vector(src, wide), _row_engine(src, wide), src, wide)


@pytest.mark.parametrize("src", FORMULAS)
def test_vector_matches_row_engine_all_null(src):
    """측정이 통째로 빠진 wafer — 전 컬럼 NULL이면 결과도 NULL이어야 한다."""
    wide = pl.DataFrame({a: pl.Series([None, None], dtype=pl.Float64)
                         for a in ALIASES})
    assert_same(_vector(src, wide), _row_engine(src, wide), src, wide)


def test_sum_of_all_nulls_is_null_not_zero():
    """polars sum_horizontal의 기본 동작(0)에 걸려 값이 조작되지 않는지."""
    wide = pl.DataFrame({"A": [None, 1.0], "B": [None, None]},
                        schema={"A": pl.Float64, "B": pl.Float64})
    assert _vector("Sum({A},{B})", wide) == [None, 1.0]
    assert _row_engine("Sum({A},{B})", wide) == [None, 1.0]


def test_division_by_zero_is_null_in_both_paths():
    wide = pl.DataFrame({"A": [1.0, 0.0, -1.0], "B": [0.0, 0.0, 0.0]})
    assert _vector("{A}/{B}", wide) == [None, None, None]
    assert _row_engine("{A}/{B}", wide) == [None, None, None]


def test_untranslatable_formula_falls_back_not_crashes():
    """번역 불가 → None을 돌려주고 apply()가 행 단위로 처리한다."""
    wide = make_wide(["A"], 10)
    assert _compile_expr("{A}+{MISSING}", set(wide.columns)) is None  # 없는 컬럼
    assert _compile_expr("Sqrt({A},{A})", set(wide.columns)) is None  # 인자 수 오류


def test_every_whitelisted_function_is_vectorized():
    """행 단위 폴백은 예외여야 한다 — 20만 행에서 폴백이 잡히면 몇 분씩 걸린다."""
    wide = make_wide(ALIASES, 10)
    for fn in ("Abs({A})", "Sqrt({A})", "Log10({A})", "Ln({A})",
               "Min({A},{B})", "Max({A},{B})", "Avg({A},{B})",
               "Sum({A},{B})", "Std({A},{B})"):
        assert _compile_expr(fn, set(wide.columns)) is not None, fn
