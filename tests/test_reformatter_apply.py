"""`reformatter.apply()` — long → long 변환의 확정 사양.

  1) REAL에 SCALE FACTOR 적용   (수식 계산 **전**)
  2) ABSOLUTE == 'Y' 이면 절대값
  3) ADDP를 시트 **행 순서대로** 계산 (아래 행이 위 행을 참조)

순서가 하나라도 어긋나면 값이 조용히 틀린다 — 스케일이 나중에 곱해지면
`{A}/{B}` 같은 비(ratio)는 맞고 `{A}-{B}` 같은 차는 틀리는 식이라
현장에서 알아채기 어렵다.
"""
from __future__ import annotations

import math

import polars as pl
import pytest

from etreport.data.reformatter import Reformatter, Rule, apply, validate
from tests.factory import make_long


def rule(cat, itemid, alias, *, absolute=False, scale=1.0, formula="", row=2):
    return Rule(category=cat, itemid=itemid, alias=alias, absolute=absolute,
                scale=scale, formula=formula, unit="", speclow=None,
                spechigh=None, target=None, row=row)


def rf_of(*rules) -> Reformatter:
    rf = Reformatter(rules=list(rules))
    validate(rf)
    assert not rf.errors
    return rf


def long_of(pairs: list[tuple[str, float | None]], key: int = 1) -> pl.DataFrame:
    """(item_id, value) 목록 → 키 하나짜리 long 프레임."""
    return pl.DataFrame({
        "root_lot_id": ["PA2600"] * len(pairs),
        "wafer_id": [f"{key:02d}"] * len(pairs),
        "item_id": [p[0] for p in pairs],
        "value": pl.Series([p[1] for p in pairs], dtype=pl.Float64),
    })


def values(out: pl.DataFrame) -> dict[str, float | None]:
    return dict(zip(out["item_id"], out["value"]))


# ── REAL ─────────────────────────────────────────────────────
def test_scale_then_absolute_then_rename():
    rf = rf_of(
        rule("REAL", "ET_A", "A", scale=1000.0),
        rule("REAL", "ET_B", "B", scale=1000.0, absolute=True),
    )
    out = apply(rf, long_of([("ET_A", -1.5e-3), ("ET_B", -1.5e-3)]))
    assert values(out) == {"A": pytest.approx(-1.5), "B": pytest.approx(1.5)}


def test_items_not_in_reformatter_are_dropped():
    rf = rf_of(rule("REAL", "ET_A", "A"))
    out = apply(rf, long_of([("ET_A", 1.0), ("ET_ZZZ", 9.0)]))
    assert values(out) == {"A": 1.0}


def test_et_value_column_is_normalized():
    """추출 원본 컬럼명은 et_value — 내부 표준 value로 바뀌어야 한다."""
    rf = rf_of(rule("REAL", "ET_A", "A", scale=2.0))
    df = long_of([("ET_A", 3.0)]).rename({"value": "et_value"})
    out = apply(rf, df)
    assert "value" in out.columns and "et_value" not in out.columns
    assert values(out) == {"A": 6.0}


def test_key_columns_are_preserved():
    rf = rf_of(rule("REAL", "ET_A", "A"))
    src = make_long(["ET_A"], lots=1, wafers=2, chips=2, seed=0, null_rate=0.0)
    out = apply(rf, src)
    assert set(out.columns) == set(src.columns) - {"et_value"} | {"value"}
    assert out.height == src.height


# ── ADDP ─────────────────────────────────────────────────────
def test_addp_uses_scaled_values():
    """스케일이 수식보다 **먼저** 적용되는지 — 차(difference)로 확인한다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A", scale=1000.0),
        rule("REAL", "ET_B", "B", scale=1.0),
        rule("ADDP", "", "D", formula="{A}-{B}", row=4),
    )
    out = apply(rf, long_of([("ET_A", 0.002), ("ET_B", 1.0)]))
    assert values(out)["D"] == pytest.approx(2.0 - 1.0)   # 스케일 후: 2 - 1


def test_addp_absolute_applies_to_result():
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("ADDP", "", "D", formula="{A}-{B}", absolute=True, row=4),
    )
    assert values(apply(rf, long_of([("ET_A", 1.0), ("ET_B", 3.0)])))["D"] == 2.0


def test_addp_chain_follows_sheet_order():
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "D1", formula="{A}*2", row=3),
        rule("ADDP", "", "D2", formula="{D1}+1", row=4),
        rule("ADDP", "", "D3", formula="{D2}*{D1}", row=5),
    )
    v = values(apply(rf, long_of([("ET_A", 3.0)])))
    assert (v["D1"], v["D2"], v["D3"]) == (6.0, 7.0, 42.0)


def test_std_addp_matches_excel_stdev():
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("REAL", "ET_C", "C"),
        rule("ADDP", "", "SP", formula="Std({A},{B},{C})", row=5),
    )
    v = values(apply(rf, long_of([("ET_A", 1.0), ("ET_B", 3.0), ("ET_C", 5.0)])))
    assert v["SP"] == pytest.approx(2.0)                 # STDEV(1,3,5) = 2


def test_null_results_are_dropped_not_zero_filled():
    """미측정 → NULL → 행 자체가 빠진다. 0으로 채우면 plot이 오염된다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("ADDP", "", "R", formula="{A}/{B}", row=4),
        rule("ADDP", "", "S", formula="Sum({A},{B})", row=5),
    )
    out = apply(rf, long_of([("ET_A", None), ("ET_B", 0.0)]))
    v = values(out)
    assert "R" not in v            # 분모 0 → NULL → drop
    assert "A" not in v            # 미측정 → drop
    assert v["S"] == 0.0           # B=0은 측정값이므로 살아 있다
    assert out["value"].null_count() == 0


def test_addp_with_unmeasured_reference_falls_back_and_yields_null(caplog):
    """참조 item이 그 청크에 아예 없으면 행 단위 폴백 → 값 없음(제외)."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("ADDP", "", "D", formula="{A}+{B}", row=4),
    )
    with caplog.at_level("INFO"):
        out = apply(rf, long_of([("ET_A", 1.0)]))       # B는 측정 안 됨
    v = values(out)
    assert v == {"A": 1.0}
    assert "행 단위로 계산" in caplog.text


def test_reformatter_without_addp_skips_pivot():
    rf = rf_of(rule("REAL", "ET_A", "A", scale=2.0))
    out = apply(rf, long_of([("ET_A", 1.0), ("ET_A", 2.0)]))
    assert sorted(out["value"]) == [2.0, 4.0]


def test_progress_callback_reports_stages():
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "D", formula="{A}*2", row=3),
    )
    seen: list[tuple] = []
    apply(rf, long_of([("ET_A", 1.0)]), on_progress=lambda *a: seen.append(a))
    stages = [s for s, _d, _t in seen]
    assert any("REAL" in s for s in stages)
    assert any(s == "ADDP D" for s in stages)
    assert any("피벗" in s for s in stages)


# ── 여러 키·여러 wafer ───────────────────────────────────────
def test_addp_computed_per_key_not_globally():
    """ADDP는 키(=측정 포인트)마다 계산된다 — wafer를 섞으면 안 된다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("ADDP", "", "D", formula="{A}-{B}", row=4),
    )
    src = pl.concat([long_of([("ET_A", 10.0), ("ET_B", 1.0)], key=1),
                     long_of([("ET_A", 20.0), ("ET_B", 2.0)], key=2)])
    out = apply(rf, src).filter(pl.col("item_id") == "D")
    got = dict(zip(out["wafer_id"], out["value"]))
    assert got == {"01": 9.0, "02": 18.0}


def test_matches_row_engine_on_realistic_frame():
    """합성 testset 한 판을 통째로 행 단위 엔진과 대조한다."""
    from etreport.data.reformatter import _STD_CALL, compile_formula
    from tests.factory import make_reformatter

    rf = make_reformatter(n_real=30, n_addp=8, seed=5)
    src = make_long([r.itemid for r in rf.reals()],
                    lots=1, wafers=3, chips=4, seed=5, null_rate=0.15)
    out = apply(rf, src)

    key = ["root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos"]
    wide = out.pivot(on="item_id", index=key, values="value",
                     aggregate_function="first")
    for r in wide.iter_rows(named=True):
        env = {k: v for k, v in r.items() if k not in key}
        for rule_ in rf.addps():
            # 순수-ref Std()는 apply()가 5키(§10.8 그룹) 단위 표본표준편차로
            # 다시 계산한다 — 행 단위 엔진과 의미가 다르므로 여기서 비교하지
            # 않는다. 그 계약은 test_reformatter_std_group.py가 지킨다.
            if _STD_CALL.search(rule_.formula):
                continue
            expect = compile_formula(rule_.formula)(env)
            if expect is not None and rule_.absolute:
                expect = abs(expect)
            got = r.get(rule_.alias)
            if expect is None or not math.isfinite(expect):
                assert got is None, (rule_.alias, got, expect)
            else:
                assert got == pytest.approx(expect, rel=1e-9), rule_.alias
