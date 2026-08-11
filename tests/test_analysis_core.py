"""화면·PPT가 공유하는 규칙들 — 축 범위, 집계, 자릿수.

plot과 표는 현장에서 이미 정상 동작을 확인했다. 여기 테스트는 그 동작을
**고정**하는 용도다(리포메터 손질이 화면 값을 건드리지 않도록).
"""
from __future__ import annotations

import polars as pl
import pytest

from etreport.model.aggregate import offspec, ref_values, wafer_stats
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


def test_offspec():
    r = rule("REAL", "ET_A", "A")
    r.speclow, r.spechigh = 0.0, 10.0
    assert offspec(-0.1, r) is True
    assert offspec(10.1, r) is True
    assert offspec(5.0, r) is False
    assert offspec(None, r) is False
    assert offspec(5.0, None) is False
