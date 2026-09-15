"""기하(W/L) trend + scatter Mode 렌더링 검증.

scatter는 die 레벨(site)인데 trend는 x가 리포메터 기하값(W/L)이라
축 계산·포인트·라인이 전부 다르다 — 이 파일이 그 계약을 고정한다.
"""
from __future__ import annotations

import polars as pl
import pytest

from etreport.data.reformatter import Reformatter, Rule, validate
from etreport.model.specs import GroupStyle, PlotSpec
from etreport.render import mpl_renderer
from etreport.render.mpl_renderer import REF_COLOR, SPEC_COLOR, TARGET_COLOR


def rf_of_wl() -> Reformatter:
    rf = Reformatter(rules=[
        Rule(category="REAL", itemid="i1", alias="A", absolute=False,
             scale=1.0, formula="", unit="V", speclow=0.2, spechigh=1.4,
             target=0.8, row=2, w=0.34, l=12.5),
        Rule(category="REAL", itemid="i2", alias="B", absolute=False,
             scale=1.0, formula="", unit="V", speclow=0.1, spechigh=1.1,
             target=None, row=3, w=0.66, l=25.0),
    ])
    validate(rf)
    assert not rf.errors
    return rf


def wide_of() -> pl.DataFrame:
    return pl.DataFrame({
        "key": [f"k{i}" for i in range(8)],
        "lot": ["L1"] * 4 + ["L2"] * 4,
        "wafer": ["W1", "W1", "W2", "W2", "W3", "W3", "W4", "W4"],
        "gid": [""] * 8,
        "A": [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        "B": [1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6],
    })


def _one_style() -> list[GroupStyle]:
    return [GroupStyle(gid="", name="전체")]


def _fig(spec: PlotSpec, df: pl.DataFrame, styles=None) -> object:
    from etreport.render.mpl_renderer import render

    rf = rf_of_wl()
    data = {"": df}
    return render(spec, data, styles or _one_style(), rf, [], (4, 3))


# ── trend 기본 ─────────────────────────────────────────────
def _lines(ax):
    return [ln for ln in ax.lines
            if ln.get_linestyle() not in ("None", "none")
            and ln.get_marker() not in ("None", "none")]


def _xmarks(ax):
    return [ln for ln in ax.lines if ln.get_linestyle() in ("None", "none")
            and ln.get_color() == TARGET_COLOR]


def _vlines(ax):
    return [ln for ln in ax.lines if ln.get_linestyle() == "--"
            and ln.get_color() == SPEC_COLOR]


def test_trend_renders_with_line_and_points():
    spec = PlotSpec(type="trend", x="W", y="A,B", mode="site")
    fig = _fig(spec, wide_of())
    ax = fig.axes[0]
    assert ax.get_xlabel() == "W"
    lines = _lines(ax)
    assert len(lines) == 1
    xs, ys = lines[0].get_xdata(), lines[0].get_ydata()
    assert list(xs) == [0.34, 0.66]          # x_i 정렬 (w 오름차순)
    assert len(ys) == 2


def test_trend_x_label_is_geom():
    spec = PlotSpec(type="trend", x="L", y="A,B", mode="site")
    ax = _fig(spec, wide_of()).axes[0]
    assert ax.get_xlabel() == "L"


def test_trend_skips_item_without_geometry():
    rf = Reformatter(rules=[
        Rule(category="REAL", itemid="i1", alias="A", absolute=False,
             scale=1.0, formula="", unit="V", speclow=None, spechigh=None,
             target=None, row=2),
        Rule(category="REAL", itemid="i2", alias="B", absolute=False,
             scale=1.0, formula="", unit="V", speclow=None, spechigh=None,
             target=None, row=3, w=0.66, l=25.0),
    ])
    validate(rf)
    data = {"": wide_of()}
    fig = mpl_renderer.render(PlotSpec(type="trend", x="W", y="A,B"),
                              data, _one_style(), rf, [], (4, 3))
    ax = fig.axes[0]
    lines = _lines(ax)
    assert len(lines) == 1
    assert list(lines[0].get_xdata()) == [0.66]


def test_trend_target_and_spec_markers():
    """타깃은 그리고, **X축 위치의 규격 세로 점선은 그리지 않는다**(사용자 확정).

    규격은 y값의 한계라 WIDTH·LENGTH 위치에 세로선을 그으면 의미 없는 격자만
    늘어난다. 예전에는 item마다 low/high 두 줄씩 그렸다.
    """
    spec = PlotSpec(type="trend", x="W", y="A,B", mode="site")
    ax = _fig(spec, wide_of()).axes[0]
    # 타깃 X 마커는 linestyle none — A만 target=0.8
    xmarks = _xmarks(ax)
    assert len(xmarks) == 1
    assert xmarks[0].get_xdata()[0] == 0.34
    assert xmarks[0].get_ydata()[0] == 0.8
    assert _vlines(ax) == []


def test_trend_mode_aggregates_to_wafer_level():
    spec = PlotSpec(type="trend", x="W", y="A,B", mode="med")
    ax = _fig(spec, wide_of()).axes[0]
    colls = list(ax.collections)
    assert len(colls) >= 2          # item 2개 스트립
    for c in colls:
        offsets = c.get_offsets()
        assert len(offsets) <= 4    # wafer 4장 이하로 집계


def test_trend_ref_group_gray_diamond():
    from etreport.render.mpl_renderer import render

    rf = rf_of_wl()
    df = wide_of()
    styles = [GroupStyle(gid="", name="기준", ref=True)]
    fig = render(PlotSpec(type="trend", x="W", y="A,B"), {"": df},
                 styles, rf, [], (4, 3))
    ax = fig.axes[0]
    lines = _lines(ax)
    assert len(lines) == 1
    assert lines[0].get_color() == REF_COLOR
    assert lines[0].get_marker() == "d"


def test_trend_ignores_logx_and_keeps_linear():
    spec = PlotSpec(type="trend", x="W", y="A,B", mode="site",
                    logx_mode="log", logy_mode="linear")
    ax = _fig(spec, wide_of()).axes[0]
    assert ax.get_xscale() == "linear"


# ── scatter Mode ───────────────────────────────────────────
def test_scatter_mode_avg_aggregates_per_wafer():
    from etreport.render.mpl_renderer import render

    rf = rf_of_wl()
    df = wide_of()
    fig = render(PlotSpec(x="A", y="B", mode="avg"), {"": df},
                 _one_style(), rf, [], (4, 3))
    ax = fig.axes[0]
    # 점을 무엇으로 그렸는지(Line2D/PathCollection)는 성능 문제이지 계약이
    # 아니다 — 세는 일은 렌더러의 `point_xy` 하나가 한다.
    assert len(mpl_renderer.point_xy(ax)) == 4   # wafer 4장


def test_scatter_mode_avg_uses_rf_agg():
    from etreport.render.mpl_renderer import render

    rf = rf_of_wl()
    df = wide_of()
    fig = render(PlotSpec(x="A", y="B", mode="avg"), {"": df},
                 _one_style(), rf, [], (4, 3))
    ax = fig.axes[0]
    pts = sorted((round(x, 6), round(y, 6))
                 for x, y in mpl_renderer.point_xy(ax))
    # wafer 평균: (1.2+1.4)/2, (1.6+1.8)/2, ...
    assert pts == [(0.35, 1.3), (0.55, 1.7), (0.75, 2.1), (0.95, 2.5)]


def test_scatter_mode_std_aggregates_per_wafer():
    from etreport.render.mpl_renderer import render

    rf = rf_of_wl()
    df = wide_of()
    fig = render(PlotSpec(x="A", y="B", mode="std"), {"": df},
                 _one_style(), rf, [], (4, 3))
    ax = fig.axes[0]
    assert len(mpl_renderer.point_xy(ax)) == 4


def test_scatter_site_keeps_die_level():
    from etreport.render.mpl_renderer import render

    rf = rf_of_wl()
    df = wide_of()
    fig = render(PlotSpec(x="A", y="B", mode="site"), {"": df},
                 _one_style(), rf, [], (4, 3))
    ax = fig.axes[0]
    assert len(mpl_renderer.point_xy(ax)) == 8   # die 8개


# ── ref_band 제거 회귀 ─────────────────────────────────────
def test_ref_band_field_removed():
    with pytest.raises(TypeError):
        PlotSpec(x="A", y="B", ref_band=True)


def test_renderer_module_has_no_ref_band():
    assert not hasattr(mpl_renderer, "REF_FILL")
    with open(mpl_renderer.__file__, encoding="utf-8") as f:
        assert "ref_band" not in f.read()
