"""화면·PPT가 공유하는 규칙들 — 축 범위, 집계, 자릿수.

plot과 표는 현장에서 이미 정상 동작을 확인했다. 여기 테스트는 그 동작을
**고정**하는 용도다(리포메터 손질이 화면 값을 건드리지 않도록).
"""
from __future__ import annotations

import math

import polars as pl
import pytest

from etreport.model.aggregate import group_representatives, offspec, ref_values, wafer_stats
from etreport.model.specs import PlotSpec, fmt_value
from etreport.render.ranges import compute_range, is_log, resolve_axes, resolve_log
from tests.test_reformatter_apply import rf_of, rule


# ── 자릿수 (확정 사양) ───────────────────────────────────────
@pytest.mark.parametrize("v,expect", [
    (0.123456, "0.123"), (0.9999, "1.000"),
    (1.0, "1.00"), (9.876, "9.88"), (10.0, "10.00"),   # 10은 경계 — 2자리
    (10.01, "10.0"), (12.34, "12.3"), (1234.5, "1234.5"),
    (-0.5, "-0.500"), (-12.34, "-12.3"), (None, ""),
])
def test_fmt_value(v, expect):
    assert fmt_value(v) == expect


def test_fmt_value_delta_sign():
    assert fmt_value(1.5, delta=True) == "+1.50"
    assert fmt_value(-1.5, delta=True) == "-1.50"    # 음수는 부호를 덧붙이지 않는다
    assert fmt_value(0.0, delta=True) == "+0.000"


# ── 축 범위 (SPEC ∪ 데이터, 중심 기준 ×1.2) ──────────────────
def test_range_covers_spec_and_data():
    rf = rf_of(rule("REAL", "ET_A", "A"))
    rf.rules[0].speclow, rf.rules[0].spechigh = 0.0, 10.0
    lo, hi = compute_range(["A"], data_min=-2.0, data_max=4.0, rf=rf)
    # lo=min(0,-2)=-2, hi=max(10,4)=10 → 중심 4, 반폭 6×1.2=7.2
    assert (lo, hi) == pytest.approx((-3.2, 11.2))


def test_range_without_spec_uses_data_only():
    rf = rf_of(rule("REAL", "ET_A", "A"))
    lo, hi = compute_range(["A"], 1.0, 3.0, rf)
    assert (lo, hi) == pytest.approx((0.8, 3.2))


def test_range_degenerate_data():
    rf = rf_of(rule("REAL", "ET_A", "A"))
    assert compute_range(["A"], None, None, rf) == pytest.approx((-0.1, 1.1))
    lo, hi = compute_range(["A"], 5.0, 5.0, rf)
    assert lo < 5.0 < hi


def test_flat_range_margin_scales_with_value():
    """값이 하나뿐일 때 여백은 값 크기에 비례한다.

    ±0.5 고정이던 시절 1e-9짜리 누설은 축이 -0.6~0.6이 되어 점이 0에
    눌러붙었고, 1e6짜리 값은 여백이 없는 것이나 마찬가지였다.
    """
    rf = rf_of(rule("REAL", "ET_A", "A"))
    for v in (1e-9, 2.5, 1e6):
        lo, hi = compute_range(["A"], v, v, rf)
        assert lo < v < hi
        assert (hi - lo) == pytest.approx(v * 0.05 * 2 * 1.2)   # 값에 비례
    lo, hi = compute_range(["A"], 0.0, 0.0, rf)                 # 0 근처는 절대값
    assert (lo, hi) == pytest.approx((-0.6, 0.6))


# ── 로그 축 범위 — 여백을 decade로 준다 ──────────────────────
def test_log_range_gives_margin_in_decades():
    """선형 10% 여백은 decade가 여럿인 축에서 사실상 0이었다.

    예전에는 1e-12~1e-6이 (1e-12, 1.1e-6)이 되어 최솟값 점이 축선에 붙어
    반쯤 잘렸다. 이제 위아래로 같은 폭(0.6 decade)이 열린다.
    """
    rf = rf_of(rule("REAL", "ET_A", "A"))
    lo, hi = compute_range(["A"], 1e-12, 1e-6, rf, log_scale=True)
    assert lo < 1e-12 and hi > 1e-6
    below = math.log10(1e-12) - math.log10(lo)
    above = math.log10(hi) - math.log10(1e-6)
    assert below == pytest.approx(above)                  # 위아래 대칭
    assert below == pytest.approx(6 / 2 * 0.2)            # 폭 ×1.2 (로그 공간)


def test_log_range_does_not_waste_decades():
    """데이터가 좁으면 축도 좁게 — 예전엔 하한이 1e-4로 못박혀 있었다."""
    rf = rf_of(rule("REAL", "ET_A", "A"))
    lo, hi = compute_range(["A"], 1e-3, 1e-2, rf, log_scale=True)
    assert lo == pytest.approx(10 ** -3.1)
    assert hi == pytest.approx(10 ** -1.9)


def test_log_range_skips_nonpositive_bounds():
    """규격 하한 0(로그에 못 그린다)이어도 데이터가 보이는 범위가 나온다."""
    rf = rf_of(rule("REAL", "ET_A", "A"))
    rf.rules[0].speclow, rf.rules[0].spechigh = 0.0, 1e-5
    lo, hi = compute_range(["A"], 1e-9, 1e-6, rf, log_scale=True)
    assert 0 < lo < 1e-9 and hi > 1e-5

    # 음수가 섞여도(ABSOLUTE 안 건 누설 등) 축이 뒤집히지 않는다
    lo, hi = compute_range(["A"], -1e-6, 1e-3, rf, log_scale=True)
    assert 0 < lo < hi


def test_log_range_flat_data():
    rf = rf_of(rule("REAL", "ET_A", "A"))
    lo, hi = compute_range(["A"], 3e-9, 3e-9, rf, log_scale=True)
    assert lo < 3e-9 < hi
    assert math.log10(hi) - math.log10(lo) == pytest.approx(1.2)  # 1 decade ×1.2


def test_multi_alias_range_is_union_of_specs():
    rf = rf_of(rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"))
    rf.rules[0].speclow, rf.rules[0].spechigh = 0.0, 1.0
    rf.rules[1].speclow, rf.rules[1].spechigh = -5.0, 2.0
    lo, hi = compute_range(["A", "B"], 0.5, 0.6, rf)
    assert lo < -5.0 and hi > 2.0


@pytest.mark.parametrize("alias,expect", [
    ("Ioff_N", True), ("nIoff", False), ("VtLeakage", True),
    ("Jg_ox", True), ("Vt_lin", False), ("IOFF_P", True),
])
def test_log_patterns(alias, expect):
    assert is_log(alias, ["Ioff*", "*Leak*", "Jg*"]) is expect


def test_log_override_wins():
    assert resolve_log("linear", ["Ioff_N"], ["Ioff*"]) is False
    assert resolve_log("log", ["Vt"], ["Ioff*"]) is True
    assert resolve_log("auto", ["Vt", "Ioff_N"], ["Ioff*"]) is True


def test_resolve_axes_manual_override():
    rf = rf_of(rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"))
    spec = PlotSpec(x="A", y="B", range_mode="manual",
                    xmin=0.0, xmax=1.0, ymin=-1.0, ymax=1.0)
    (xlo, xhi, lgx), (ylo, yhi, _) = resolve_axes(
        spec, rf, [], {"A": (5.0, 6.0), "B": (5.0, 6.0)})
    assert (xlo, xhi, ylo, yhi) == (0.0, 1.0, -1.0, 1.0)
    assert lgx is False


# ── 집계 ─────────────────────────────────────────────────────
@pytest.fixture
def points():
    return pl.DataFrame({
        "key": ["k1", "k2", "k3", "k4"],
        "lot": ["L1", "L1", "L1", "L1"],
        "wafer": ["01", "01", "02", "02"],
        "gid": ["g1", "g1", "g2", "g2"],
        "A": [1.0, 3.0, 10.0, 20.0],
        "B": [1.0, None, 2.0, 4.0],
    })


@pytest.fixture
def trend_points():
    """trend 라인 대표값용 wide 프레임 — lot 2 × wafer 2 × die 3, 일부 NULL."""
    return pl.DataFrame({
        "key": [f"k{i}" for i in range(1, 13)],
        "lot": ["L1"] * 6 + ["L2"] * 6,
        "wafer": ["01", "01", "01", "02", "02", "02"] * 2,
        "gid": ["g1"] * 12,
        "A": [1.0, 3.0, 9.0, 5.0, 7.0, None,
              10.0, None, 20.0, 20.0, 30.0, 40.0],
        "B": [2.0, None, 4.0, 6.0, None, 8.0,
              8.0, 12.0, 10.0, 100.0, 200.0, None],
    })


def test_wafer_stats_mean(points):
    st = wafer_stats(points, set(), ["A", "B"])
    assert st.get("A", "L1", "01") == 2.0
    assert st.get("A", "L1", "02") == 15.0
    assert st.get("B", "L1", "01") == 1.0          # NULL 제외 평균
    assert st.get("A", "L1", "없음") is None


def test_wafer_stats_std_is_sample(points):
    st = wafer_stats(points, set(), ["A"], agg="std")
    assert st.get("A", "L1", "01") == pytest.approx(2 ** 0.5)


def test_wafer_stats_counts_exclusions(points):
    st = wafer_stats(points, {"k1"}, ["A"])
    assert st.get("A", "L1", "01") == 3.0          # 남은 1점
    assert st.ex("L1", "01") == 1
    assert st.ex("L1", "02") == 0


def test_ref_values(points):
    assert ref_values(points, set(), "g2", ["A"])["A"] == 15.0
    assert ref_values(points, {"k3"}, "g2", ["A"])["A"] == 20.0
    assert ref_values(points, set(), "", ["A"]) == {}        # REF 그룹 미지정
    assert ref_values(points, set(), None, ["A"]) == {}


# ── trend: 중앙값 + 그룹 대표값 ────────────────────────────────
def test_wafer_stats_median(trend_points):
    st = wafer_stats(trend_points, set(), ["A", "B"], agg="med")
    assert st.get("A", "L1", "01") == 3.0     # [1, 3, 9]
    assert st.get("A", "L1", "02") == 6.0     # [5, 7]
    assert st.get("A", "L2", "01") == 15.0    # [10, 20]
    assert st.get("A", "L2", "02") == 30.0    # [20, 30, 40]
    assert st.get("B", "L1", "01") == 3.0     # [2, 4] — NULL 제외
    assert st.get("B", "L2", "01") == 10.0    # [8, 12, 10]
    assert st.get("A", "L9", "99") is None


def test_group_representatives_med(trend_points):
    reps = group_representatives(trend_points, set(), ["A", "B"], agg="med")
    assert reps["A"] == pytest.approx((3.0 + 6.0 + 15.0 + 30.0) / 4)
    assert reps["B"] == pytest.approx((3.0 + 7.0 + 10.0 + 150.0) / 4)


def test_group_representatives_avg(trend_points):
    reps = group_representatives(trend_points, set(), ["A"], agg="avg")
    assert reps["A"] == pytest.approx((13 / 3 + 6.0 + 15.0 + 30.0) / 4)


def test_group_representatives_std(trend_points):
    reps = group_representatives(trend_points, set(), ["A"], agg="std")
    expect = (math.sqrt(52 / 3) + math.sqrt(2) + math.sqrt(50) + 10.0) / 4
    assert reps["A"] == pytest.approx(expect)


def test_group_representatives_excluded(trend_points):
    # k3(A=9) 제외 → (L1,01) A는 [1, 3] → 중앙값 2.0
    reps = group_representatives(trend_points, {"k3"}, ["A"], agg="med")
    assert reps["A"] == pytest.approx((2.0 + 6.0 + 15.0 + 30.0) / 4)


def test_group_representatives_wafer_all_excluded(trend_points):
    # (L1,01) die 전부 제외 → 남은 wafer 3장의 평균
    reps = group_representatives(trend_points, {"k1", "k2", "k3"}, ["A"], agg="med")
    assert reps["A"] == pytest.approx((6.0 + 15.0 + 30.0) / 3)


def test_group_representatives_no_values(trend_points):
    empty = trend_points.filter(pl.col("lot") == "없음")
    assert group_representatives(empty, set(), ["A"]) == {"A": None}


def test_ref_values_median(trend_points):
    # A 전체 정렬 [1,3,5,7,9,10,20,20,30,40] → (9+10)/2, k1 제외 시 10.0
    assert ref_values(trend_points, set(), "g1", ["A"], agg="med")["A"] == 9.5
    assert ref_values(trend_points, {"k1"}, "g1", ["A"], agg="med")["A"] == 10.0


def test_offspec():
    r = rule("REAL", "ET_A", "A")
    r.speclow, r.spechigh = 0.0, 10.0
    assert offspec(-0.1, r) is True
    assert offspec(10.1, r) is True
    assert offspec(5.0, r) is False
    assert offspec(None, r) is False
    assert offspec(5.0, None) is False
