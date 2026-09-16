"""요청 ⑤ — ADDP `Std()`의 6키 그룹 표본표준편차(n-1) 계약.

`reformatter.apply()`는 Std 호출(인자는 {ALIAS} 또는 Abs({A}) 같은 식)을
root_lot_id·wafer_id·step_id·step_seq·total_site_cnt·temperature 6키 그룹의 표본 std로
다시 계산해 그룹의 모든 행에 흩뿌린다(broadcast). 6키 또는 인자 컬럼이
데이터에 없으면 예전 행 단위(수평) Std로 폴백한다.

이 계약은 표·plot·PPT의 "그룹 산포" 지표와 화면/행 단위 엔진 비교
(`test_matches_row_engine_on_realistic_frame`)가 여기를 따르는 근거다.
"""
from __future__ import annotations

import logging
import math

import polars as pl
import pytest

from etreport.data.reformatter import Reformatter, Rule, apply, validate


def rule(cat, itemid, alias, *, absolute=False, scale=1.0, formula="", row=2):
    return Rule(category=cat, itemid=itemid, alias=alias, absolute=absolute,
                scale=scale, formula=formula, unit="", speclow=None,
                spechigh=None, target=None, row=row)


def rf_of(*rules) -> Reformatter:
    rf = Reformatter(rules=list(rules))
    validate(rf)
    assert not rf.errors
    return rf


def long5(rows, item_col, values):
    """6키 + 칩 좌표 + tkout_time를 갖춘 long 프레임.

    칩 좌표·tkout_time이 없으면 피벗이 wafer당 한 행으로 뭉개 첫 값만
    남기므로(aggregate_function="first") 그룹 std 테스트에 쓸 수 없다.
    rows = (lot, wafer, step_id, step_seq, temperature[, total_site_cnt])
    하나당 칩 한 개. total_site_cnt를 생략하면 5.
    """
    n = len(rows)
    return pl.DataFrame({
        "root_lot_id": [r[0] for r in rows],
        "wafer_id": [r[1] for r in rows],
        "step_id": [r[2] for r in rows],
        "step_seq": [r[3] for r in rows],
        "temperature": [r[4] for r in rows],
        "total_site_cnt": [r[5] if len(r) > 5 else 5 for r in rows],
        "chip_x_pos": list(range(n)),
        "chip_y_pos": [0] * n,
        "tkout_time": [float(i) for i in range(n)],
        "item_id": [item_col] * n,
        "value": pl.Series(values, dtype=pl.Float64),
    })


def s_values(out: pl.DataFrame) -> pl.DataFrame:
    """item_id == ADDP 결과(S) 행만 — 웨이퍼·칩 구분 없이 값을 본다."""
    return out.filter(pl.col("item_id") == "S")


def test_std_broadcasts_group_std_to_every_member():
    """Std는 wafer(6키) 그룹 단위로 계산해 그룹의 모든 행에 흩뿌린다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "S", formula="Std({A})", row=3),
    )
    src = pl.concat([
        long5([("PA2600", "01", "M2", 1, 25.0)] * 4, "ET_A",
              [1.0, 3.0, 7.0, 9.0]),          # std(1,3,7,9) = sqrt(40/3)
        long5([("PA2600", "02", "M2", 1, 25.0)] * 2, "ET_A",
              [2.0, 4.0]),                    # std(2,4) = sqrt(2)
    ])
    out = apply(rf, src)
    w1 = s_values(out).filter(pl.col("wafer_id") == "01")["value"].to_list()
    w2 = s_values(out).filter(pl.col("wafer_id") == "02")["value"].to_list()
    assert w1 == [pytest.approx(math.sqrt(40 / 3))] * 4   # broadcast
    assert w2 == [pytest.approx(math.sqrt(2))] * 2


def test_std_pools_multiple_arguments_into_one_group():
    """Std({A},{B})는 같은 그룹 안에서 A·B 값을 한데 모아 std를 낸다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("ADDP", "", "S", formula="Std({A},{B})", row=4),
    )
    rows = [("PA2600", "01", "M2", 1, 25.0)] * 2
    src = pl.concat([
        long5(rows, "ET_A", [1.0, 3.0]),
        long5(rows, "ET_B", [7.0, 9.0]),
    ])
    out = apply(rf, src)
    vals = s_values(out)["value"].to_list()
    assert vals == [pytest.approx(math.sqrt(40 / 3))] * 2


def test_std_with_fewer_than_two_valid_is_dropped():
    """유효값이 2개 미만인 그룹은 STDEV와 같이 NULL — 행이 빠진다(0 아님)."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "S", formula="Std({A})", row=3),
    )
    src = pl.concat([
        long5([("PA2600", "01", "M2", 1, 25.0)], "ET_A", [5.0]),  # 유효 1개
        long5([("PA2600", "02", "M2", 1, 25.0)] * 2, "ET_A",
              [3.0, 5.0]),
    ])
    out = apply(rf, src)
    s = s_values(out)
    assert s.filter(pl.col("wafer_id") == "01").is_empty()         # S 행 없음
    w2 = s.filter(pl.col("wafer_id") == "02")["value"].to_list()
    assert w2 == [pytest.approx(math.sqrt(2))] * 2
    # A는 그대로 살아 있다 — NULL이 된 건 S 행뿐이다
    assert out.filter(pl.col("item_id") == "A").height == 3


def test_std_ignores_null_values_within_a_group():
    """미측정(NULL)은 std에서 제외되고 나머지 값들로 계산한다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "S", formula="Std({A})", row=3),
    )
    src = long5([("PA2600", "01", "M2", 1, 25.0)] * 4, "ET_A",
                [1.0, None, 7.0, 9.0])        # std(1,7,9) = sqrt(52/3)
    out = apply(rf, src)
    vals = s_values(out)["value"].to_list()
    assert vals == [pytest.approx(math.sqrt(52 / 3))] * 4


@pytest.mark.parametrize("idx,lo,hi,exp", [
    (2, "M1", "M2", math.sqrt(2)),        # step_id 분리: (1,3) vs (1,9)
    (3, 1, 2, math.sqrt(2)),
    (4, 25.0, 125.0, math.sqrt(2)),
])
def test_std_splits_groups_by_measurement_condition(idx, lo, hi, exp):
    """step·step_seq·온도가 다르면 같은 wafer라도 그룹이 갈린다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "S", formula="Std({A})", row=3),
    )
    r_lo = ("PA2600", "01", lo if idx == 2 else "M2",
            lo if idx == 3 else 1, lo if idx == 4 else 25.0)
    r_hi = ("PA2600", "01", hi if idx == 2 else "M2",
            hi if idx == 3 else 1, hi if idx == 4 else 125.0)
    rows = [r_lo, r_lo, r_hi, r_hi]      # 조건별 칩 2개
    src = long5(rows, "ET_A", [1.0, 3.0, 1.0, 9.0])
    out = apply(rf, src)
    vals = s_values(out)["value"].unique().sort().to_list()
    assert vals == pytest.approx([exp, math.sqrt(32)])


def test_std_rewrite_keeps_surrounding_arithmetic():
    """Std 호출만 스캐폴드로 바뀌고 나머지 산술({__stdN}*{C})은 그대로."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("REAL", "ET_C", "C"),
        rule("ADDP", "", "S", formula="Std({A},{B})*{C}", row=5),
    )
    rows = [("PA2600", "01", "M2", 1, 25.0)] * 2
    src = pl.concat([
        long5(rows, "ET_A", [1.0, 3.0]),
        long5(rows, "ET_B", [7.0, 9.0]),
        long5(rows, "ET_C", [2.0, 2.0]),
    ])
    out = apply(rf, src)
    vals = s_values(out)["value"].to_list()
    assert vals == [pytest.approx(math.sqrt(40 / 3) * 2)] * 2


def test_std_same_args_in_one_formula_share_one_scaffold():
    """같은 인자 조합은 한 번만 만들어 재사용한다(캐시)."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("ADDP", "", "S", formula="Std({A},{B})*2+Std({A},{B})", row=4),
    )
    rows = [("PA2600", "01", "M2", 1, 25.0)] * 2
    src = pl.concat([
        long5(rows, "ET_A", [1.0, 3.0]),
        long5(rows, "ET_B", [7.0, 9.0]),
    ])
    out = apply(rf, src)
    vals = s_values(out)["value"].to_list()
    assert vals == [pytest.approx(math.sqrt(40 / 3) * 3)] * 2


def test_std_can_reference_an_earlier_addp():
    """시트 행 순서 규칙 — 아래 ADDP(Std)가 위 ADDP를 참조할 수 있다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "D", formula="{A}*2", row=3),
        rule("ADDP", "", "S", formula="Std({D})", row=4),
    )
    rows = [("PA2600", "01", "M2", 1, 25.0)] * 2
    src = long5(rows, "ET_A", [1.0, 3.0])     # D = (2,6) → std = sqrt(8)
    out = apply(rf, src)
    vals = s_values(out)["value"].to_list()
    assert vals == [pytest.approx(math.sqrt(8))] * 2


def test_std_addp_absolute_applies_to_group_result():
    """ABSOLUTE=True여도 그룹 std 결과에 절대값 경로가 돈다(값은 동일)."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "S", formula="Std({A})", absolute=True, row=3),
    )
    src = long5([("PA2600", "01", "M2", 1, 25.0)] * 2, "ET_A",
                [1.0, 3.0])
    vals = s_values(apply(rf, src))["value"].to_list()
    assert vals == [pytest.approx(math.sqrt(2))] * 2


def test_std_falls_back_to_row_engine_without_five_keys(caplog):
    """6키가 데이터에 없으면 기존 행 단위 Std로 폴백하고 로그에 남긴다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "S", formula="Std({A})", row=3),
    )
    src = long5([("PA2600", "01", "M2", 1, 25.0)] * 2, "ET_A",
                [1.0, 3.0]).drop("step_id")
    with caplog.at_level(logging.INFO, logger="etreport.data.reformatter"):
        out = apply(rf, src)
    assert "예전처럼 행 단위로 계산합니다" in caplog.text
    # 행 단위는 칩별 값 하나뿐 → 유효 1개 → NULL → S 행 전체가 빠진다
    assert s_values(out).is_empty()
    assert out.filter(pl.col("item_id") == "A").height == 2


def test_scaffold_columns_do_not_leak_into_output():
    """__stdN 스캐폴드 컬럼이 출력 item_id로 새지 않는다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("ADDP", "", "S", formula="Std({A},{B})+{A}", row=4),
    )
    rows = [("PA2600", "01", "M2", 1, 25.0)] * 2
    src = pl.concat([
        long5(rows, "ET_A", [1.0, 3.0]),
        long5(rows, "ET_B", [7.0, 9.0]),
    ])
    out = apply(rf, src)
    assert not any(c.startswith("__std") for c in out.columns)
    item_ids = out["item_id"].unique().to_list()
    assert set(item_ids) <= {"A", "B", "S"}


def test_std_splits_by_total_site_cnt():
    """total_site_cnt가 다르면 다른 묶음이다(6키)."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "S", formula="Std({A})", row=3),
    )
    src = pl.concat([
        long5([("PA2600", "01", "M2", 1, 25.0, 5)] * 2, "ET_A", [1.0, 3.0]),
        long5([("PA2600", "01", "M2", 1, 25.0, 9)] * 2, "ET_A", [10.0, 30.0]),
    ])
    s = s_values(apply(rf, src))
    assert s.filter(pl.col("total_site_cnt") == 5)["value"].to_list() == \
        [pytest.approx(math.sqrt(2))] * 2
    assert s.filter(pl.col("total_site_cnt") == 9)["value"].to_list() == \
        [pytest.approx(math.sqrt(200))] * 2


def test_std_survives_null_key():
    """키 하나(온도)가 NULL이어도 그 묶음의 std가 붙는다 — join이 NULL 키를
    다르게 보면 결과가 전부 NULL이 되어 item이 통째로 사라졌다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "S", formula="Std({A})", row=3),
    )
    src = long5([("PA2600", "01", "M2", 1, None)] * 2, "ET_A", [1.0, 3.0])
    assert s_values(apply(rf, src))["value"].to_list() == \
        [pytest.approx(math.sqrt(2))] * 2


@pytest.mark.parametrize("fn", ["stddev", "STDDEV", "StdDev", "stdev", "STDEV"])
def test_stddev_alias_is_group_std(fn):
    """엑셀 STDEV·SQL STDDEV 표기도 Std와 같다 — 예전엔 검증에서 행이 빠졌다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("ADDP", "", "S", formula=f"{fn}({{A}},{{B}})", row=4),
    )
    rows = [("PA2600", "01", "M2", 1, 25.0)] * 2
    src = pl.concat([
        long5(rows, "ET_A", [1.0, 3.0]),
        long5(rows, "ET_B", [7.0, 9.0]),
    ])
    assert s_values(apply(rf, src))["value"].to_list() == \
        [pytest.approx(math.sqrt(40 / 3))] * 2


def test_std_repeated_argument_does_not_crash():
    rf = rf_of(
        rule("REAL", "ET_A", "A"),
        rule("ADDP", "", "S", formula="Std({A},{A})", row=3),
    )
    src = long5([("PA2600", "01", "M2", 1, 25.0)] * 2, "ET_A", [1.0, 3.0])
    assert s_values(apply(rf, src)).height == 2


def test_std_expression_args_are_group_std():
    """Std(Abs({A}),{B})도 wafer 묶음 산포다 — 인자 식은 칩마다 먼저 계산한다."""
    rf = rf_of(
        rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"),
        rule("ADDP", "", "S", formula="stddev(Abs({A}),{B}*2)", row=4),
    )
    rows = [("PA2600", "01", "M2", 1, 25.0)] * 2
    src = pl.concat([
        long5(rows, "ET_A", [-1.0, -3.0]),     # Abs → 1, 3
        long5(rows, "ET_B", [3.5, 4.5]),       # *2  → 7, 9
    ])
    out = apply(rf, src)
    assert s_values(out)["value"].to_list() == \
        [pytest.approx(math.sqrt(40 / 3))] * 2
    assert not any(v.startswith("__std") for v in out["item_id"].unique())


def test_std_call_scanner_handles_nesting_and_alias_parens():
    from etreport.data.reformatter import _std_calls, _std_substitute
    f = "Std(Abs({A (x,y)}), Max({B},{C}))/Avg({A (x,y)})+STD({D})"
    calls = _std_calls(f)
    assert [c[2] for c in calls] == [["Abs({A (x,y)})", "Max({B},{C})"], ["{D}"]]
    assert _std_substitute(f, ["__std0", "__std1"]) == \
        "{__std0}/Avg({A (x,y)})+{__std1}"


def test_nested_std_falls_back_to_row_engine():
    from etreport.data.reformatter import STD_KEYS, _std_needs
    cols = {*STD_KEYS, "A", "B"}
    assert _std_needs("Std(Std({A}),{B})", cols) == []
    assert _std_needs("Std(Abs({A}),{B})", cols) == [["Abs({A})", "{B}"]]
