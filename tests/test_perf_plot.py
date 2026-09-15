"""plot 병목 회귀 — 설계 §4-C에서 고친 다섯 가지가 되돌아오지 않게.

여기서 지키는 것:
  1. 클릭 제외가 꺼져 있으면 히트테스트용 점을 **모으지 않는다**
     (리포트 미리보기는 슬롯 6개가 동시에 걸리는 자리다).
  2. 그룹 분할은 `partition_by` **한 번** — 그룹마다 filter를 걸면 O(n×g)다.
  3. `active()`는 캐시된다 — 같은 프레임·같은 제외 집합이면 다시 필터하지 않고,
     제외가 바뀌면 **반드시** 다시 계산한다(캐시가 틀리면 화면이 거짓말을 한다).
  4. 점은 균일 스타일이면 `Line2D`로 그린다 — 그림은 그대로다(점 수·좌표 동일).
  5. 클릭 히트테스트는 점 전체를 픽셀로 변환하지 않는다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import polars as pl
import pytest

from etreport.model.specs import GroupStyle, PlotSpec
from etreport.render import mpl_renderer


@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def state(qapp, appdata):
    from etreport import demo
    from etreport.model.state import AppState

    st = AppState()
    demo.load_demo(st)
    return st


# ── 1) 클릭 제외가 꺼져 있으면 점을 모으지 않는다 ────────────────────────
def test_points_are_collected_only_when_picking(state):
    from etreport.ui.widgets.plot_canvas import PlotCanvas

    cv = PlotCanvas(state, mini=True)
    cv.draw_spec(state.explore)
    assert cv._series == [], "on_pick이 없는데 히트테스트 좌표를 모았다"

    cv.on_pick = lambda key: None
    cv.draw_spec(state.explore)
    assert cv._series, "클릭 제외를 켰는데 좌표가 없다"
    cv.deleteLater()


def test_rebuild_leaves_no_ghost_canvases(state, qapp):
    """슬롯을 다시 만들면 **예전 캔버스가 화면에 남지 않는다.**

    `deleteLater()`만 부르면 파괴가 이벤트 루프로 밀려, 그동안 옛 캔버스 6개가
    예전 자리에 그대로 그려진다(슬롯 경계에 축 조각이 삐져나온 채 찍혔다).
    보이는 캔버스가 두 배면 그리는 값도 두 배다 — 비용 문제이기도 하다.
    """
    from etreport.model.state import StateBus
    from etreport.ui.tabs.report import ReportTab
    from etreport.ui.widgets.plot_canvas import PlotCanvas

    tab = ReportTab(state, StateBus())
    tab.resize(1000, 700)
    tab.show()
    tab.rebuild()
    first = tab.findChildren(PlotCanvas)
    assert first, "미리보기에 캔버스가 하나도 없다"
    assert len(first) == len(tab._canvases)

    for _ in range(2):                 # 같은 페이지를 다시 그린다
        tab.rebuild()
        again = tab.findChildren(PlotCanvas)
        assert len(again) == len(first), (
            f"옛 캔버스가 화면에 남았다 — {len(first)}개 → {len(again)}개")
    tab.deleteLater()


# ── 2) 그룹 분할은 한 번 ─────────────────────────────────────────────────
def test_group_split_uses_partition_by_once(state, monkeypatch):
    from etreport.ui.widgets.plot_canvas import PlotCanvas

    calls = {"filter": 0, "partition": 0}
    real_filter = pl.DataFrame.filter
    real_part = pl.DataFrame.partition_by

    def counting_filter(self, *a, **k):
        calls["filter"] += 1
        return real_filter(self, *a, **k)

    def counting_partition(self, *a, **k):
        calls["partition"] += 1
        return real_part(self, *a, **k)

    monkeypatch.setattr(pl.DataFrame, "filter", counting_filter)
    monkeypatch.setattr(pl.DataFrame, "partition_by", counting_partition)

    cv = PlotCanvas(state)
    first = cv.render_args(state.explore)       # 캐시를 데우는 첫 호출
    assert first is not None
    # 첫 호출에 도는 filter는 둘뿐이다: active() 하나, 제외 프레임 하나.
    # 그룹 수와 **무관**해야 한다(예전에는 그룹마다 하나씩이었다).
    assert calls["filter"] <= 2, calls

    calls.update(filter=0, partition=0)
    args = cv.render_args(state.explore)        # 슬롯 6개 중 두 번째 이후
    assert calls["partition"] == 1
    assert calls["filter"] == 0, "캐시가 있는데 다시 필터했다"

    # 나눈 결과가 예전(그룹마다 filter)과 **같아야** 한다 — 빠른 길이 다른
    # 그림을 그리면 아무 의미가 없다. 배정이 없는 행(gid="")은 예전에도
    # 어느 그룹에도 들어가지 않았다.
    data = args[1]
    assert len(data) >= 2                       # 데모는 그룹이 여럿이다
    active = state.active()
    for g in state.groups:
        want = real_filter(active, pl.col("gid") == g.gid)
        assert data.get(g.gid, want.clear()).height == want.height, g.gid
    cv.deleteLater()


# ── 3) active() 캐시 ─────────────────────────────────────────────────────
def test_active_is_cached_but_invalidated_by_exclusions(state):
    first = state.active()
    assert state.active() is first, "같은 조건인데 다시 계산했다"

    # 데모는 이미 두 점을 제외해 두었다 — 아직 숨지 않은 점을 골라야 한다
    hide = state.hidden()
    free = [k for k in state.data["key"].to_list() if k not in hide]
    key = str(free[0])
    state.excluded.add(key)
    second = state.active()
    assert second is not first, "제외가 바뀌었는데 옛 결과를 돌려줬다"
    assert second.height == first.height - 1

    # 한 점을 빼고 다른 점을 넣어 **개수가 같아도** 캐시가 풀려야 한다
    other = str(free[1])
    state.excluded.discard(key)
    state.excluded.add(other)
    third = state.active()
    assert third is not second
    assert set(third["key"]) != set(second["key"])
    state.excluded.discard(other)


def test_active_cache_follows_a_new_frame(state):
    before = state.active()
    state.data = state.data.with_columns(pl.lit(1).alias("_probe"))
    after = state.active()
    assert after is not before
    assert "_probe" in after.columns


# ── 4) Line2D 전환이 그림을 바꾸지 않는다 ────────────────────────────────
def test_line2d_points_match_the_data(state):
    style = GroupStyle(gid="", name="전체", color="#0071e3", symbol="o", size=6)
    items = [c for c in state.data.columns
             if c not in ("key", "lot", "wafer", "gid", "step", "temp", "site")]
    spec = PlotSpec(x=items[0], y=items[1], type="scatter", mode="site")
    df = state.data.select(["key", items[0], items[1]]).drop_nulls()
    fig = mpl_renderer.render(spec, {"": df}, [style], state.rf, [], (4, 3))
    ax = fig.axes[0]

    # 점은 하나도 버리지 않는다 — 샘플링·decimation 금지
    assert len(mpl_renderer.point_xy(ax)) == df.height
    line = next(x for x in ax.lines if x.get_gid() == mpl_renderer.POINT_GID)
    # scatter의 s(면적)와 plot의 markersize(지름) 환산: markersize = sqrt(s)
    assert line.get_markersize() == pytest.approx(style.size)
    assert line.get_linestyle() in ("none", "None", " ", "")


def test_edge_only_markers_keep_their_edge():
    """`+`·`x`는 면이 없다 — 테두리를 지우면 아무것도 안 보인다."""
    from matplotlib.figure import Figure

    ax = Figure().add_subplot(111)
    plus = mpl_renderer.points(ax, [0, 1], [0, 1], color="#000000", size=6,
                               marker="+")
    dot = mpl_renderer.points(ax, [0, 1], [0, 1], color="#000000", size=6,
                              marker="o")
    assert plus.get_markeredgewidth() > 0
    assert dot.get_markeredgewidth() == 0


# ── 5) 히트테스트는 전체를 픽셀로 변환하지 않는다 ────────────────────────
def test_hit_test_does_not_transform_every_point(state):
    """클릭 한 번에 넘기는 좌표 수가 **점 수보다 훨씬 적어야** 한다."""
    import numpy as np

    from etreport.ui.widgets.plot_canvas import PlotCanvas

    picked: list[str] = []
    cv = PlotCanvas(state)
    cv.on_pick = picked.append
    cv.draw_spec(state.explore)
    assert cv._series
    total = sum(len(keys) for _x, _y, keys in cv._series)

    ax = cv.figure.axes[0]
    sizes: list[int] = []
    real = ax.transData.transform

    def counting(arg):
        arr = np.asarray(arg)
        if arr.ndim == 2:
            sizes.append(arr.shape[0])
        return real(arg)

    ax.transData.transform = counting            # type: ignore[method-assign]
    xs, ys, _keys = cv._series[0]
    px, py = real((float(xs[0]), float(ys[0])))

    class _Ev:
        x, y = px, py

    cv._click(_Ev())
    ax.transData.transform = real                # type: ignore[method-assign]

    assert picked, "가장 가까운 점을 고르지 못했다"
    assert sum(sizes) < total, (
        f"클릭 한 번에 {sum(sizes)}점을 변환했다 — 전체는 {total}점")
    cv.deleteLater()
