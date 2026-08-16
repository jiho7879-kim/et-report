"""xy쌍을 겹쳐 그린 scatter — 규격 창과 타깃이 쌍마다 하나씩.

x·y를 컴마로 여러 개 적으면 한 plot에 쌍이 겹쳐 그려진다. 예전에는 규격을
전 쌍의 합집합(min low, max high)으로 뭉쳐 창 하나만 그렸는데, 쌍끼리 규격이
다르면 어느 쪽에도 맞지 않는 창이 나왔다. 이제 **쌍마다 한 개씩** 그린다 —
색은 쌍을 구분하지 않고 규격 빨강·타깃 파랑 그대로다.
"""
from __future__ import annotations

import polars as pl
from matplotlib.patches import Rectangle

from etreport.data.reformatter import Reformatter, Rule, validate
from etreport.model.specs import GroupStyle, PlotSpec
from etreport.render.mpl_renderer import SPEC_COLOR, TARGET_COLOR, render


def _rf() -> Reformatter:
    """A·B는 규격/타깃이 서로 다르고, C는 규격이 한쪽(하한)만 있다."""
    rf = Reformatter(rules=[
        Rule(category="REAL", itemid="i1", alias="Ax", absolute=False,
             scale=1.0, formula="", unit="V", speclow=0.2, spechigh=1.4,
             target=0.8, row=2),
        Rule(category="REAL", itemid="i2", alias="Ay", absolute=False,
             scale=1.0, formula="", unit="V", speclow=1.0, spechigh=3.0,
             target=2.0, row=3),
        Rule(category="REAL", itemid="i3", alias="Bx", absolute=False,
             scale=1.0, formula="", unit="V", speclow=5.0, spechigh=7.0,
             target=6.0, row=4),
        Rule(category="REAL", itemid="i4", alias="By", absolute=False,
             scale=1.0, formula="", unit="V", speclow=8.0, spechigh=9.0,
             target=8.5, row=5),
    ])
    validate(rf)
    assert not rf.errors
    return rf


def _df() -> pl.DataFrame:
    return pl.DataFrame({
        "key": [f"k{i}" for i in range(4)],
        "lot": ["L1"] * 4,
        "wafer": ["W1", "W1", "W2", "W2"],
        "gid": [""] * 4,
        "Ax": [0.3, 0.5, 0.7, 0.9],
        "Ay": [1.2, 1.6, 2.0, 2.4],
        "Bx": [5.2, 5.6, 6.2, 6.8],
        "By": [8.1, 8.3, 8.6, 8.9],
    })


def _fig(spec: PlotSpec):
    return render(spec, {"": _df()}, [GroupStyle(gid="", name="전체")],
                  _rf(), [], (4, 3))


def _boxes(ax) -> list[Rectangle]:
    return [p for p in ax.patches if isinstance(p, Rectangle)
            and p.get_edgecolor() is not None and not p.get_fill()]


def _xmarks(ax):
    return [ln for ln in ax.lines if ln.get_marker() == "x"
            and ln.get_color() == TARGET_COLOR]


def test_two_pairs_draw_two_spec_boxes():
    ax = _fig(PlotSpec(type="scatter", x="Ax,Bx", y="Ay,By", mode="site")).axes[0]
    boxes = _boxes(ax)
    assert len(boxes) == 2
    corners = sorted((round(b.get_x(), 6), round(b.get_y(), 6),
                      round(b.get_width(), 6), round(b.get_height(), 6))
                     for b in boxes)
    assert corners == [(0.2, 1.0, 1.2, 2.0), (5.0, 8.0, 2.0, 1.0)]


def test_spec_boxes_share_one_color():
    ax = _fig(PlotSpec(type="scatter", x="Ax,Bx", y="Ay,By", mode="site")).axes[0]
    from matplotlib.colors import to_rgba
    assert {b.get_edgecolor() for b in _boxes(ax)} == {to_rgba(SPEC_COLOR)}


def test_two_pairs_draw_two_targets():
    ax = _fig(PlotSpec(type="scatter", x="Ax,Bx", y="Ay,By", mode="site")).axes[0]
    pts = sorted((float(ln.get_xdata()[0]), float(ln.get_ydata()[0]))
                 for ln in _xmarks(ax))
    assert pts == [(0.8, 2.0), (6.0, 8.5)]


def test_single_pair_unchanged():
    ax = _fig(PlotSpec(type="scatter", x="Ax", y="Ay", mode="site")).axes[0]
    assert len(_boxes(ax)) == 1
    assert len(_xmarks(ax)) == 1


def test_same_alias_pairs_draw_one_box():
    """쌍이 여럿이라도 규격이 같으면 겹쳐 그리지 않는다(선이 굵어 보인다)."""
    ax = _fig(PlotSpec(type="scatter", x="Ax", y="Ay,Ay", mode="site")).axes[0]
    assert len(_boxes(ax)) == 1
    assert len(_xmarks(ax)) == 1
