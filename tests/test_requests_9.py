"""요청 9건(2026-09-29) — 리포트 축 설정·드래그 제외·plot별 제외·그룹 스타일·
지연 반영·step_seq 코드·PPT 색·막대그래프.

step_seq(7번)는 `test_extract_schema.py`, 지연 반영(6번)은
`test_bugfix_9.test_groups_changed_only_marks_dirty`가 지킨다.
"""
from __future__ import annotations

import pytest

from etreport.model.specs import GroupStyle, PlotSpec


@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def demo_state(appdata):
    from etreport import demo
    from etreport.model.state import AppState

    st = AppState()
    demo.load_demo(st)
    return st


class _Ev:
    def __init__(self, x, y):
        self.x, self.y, self.button = x, y, 1


def _drawn_points(fig) -> int:
    from etreport.render import mpl_renderer
    return len(mpl_renderer.point_xy(fig.axes[0]))


# ── 1·2 한쪽만 적은 수동 범위 ──────────────────────────────
def test_manual_range_overrides_only_given_bound():
    from etreport.render.ranges import manual_range

    spec = PlotSpec(range_mode="manual", ymin=0.0)
    assert manual_range(spec, "y", -3.0, 9.0) == (0.0, 9.0)
    assert manual_range(spec, "x", -3.0, 9.0) == (-3.0, 9.0)
    assert manual_range(PlotSpec(ymin=0.0), "y", -3.0, 9.0) == (-3.0, 9.0)


def test_report_slot_reads_x_scale_and_range(qapp, demo_state):
    from etreport.model.state import StateBus
    from etreport.ui.tabs.report import ReportTab

    tab = ReportTab(demo_state, StateBus())
    spec = next(s for s in demo_state.report.pages[0].slots
                if s is not None and s.type == "scatter")
    tab._select(demo_state.report.pages[0].slots.index(spec))
    tab.cmb_logx.setCurrentIndex(2)
    tab.ed_range["ymin"].setText("0")
    tab.ed_range["xmax"].setText("abc")          # 숫자가 아니면 비운다
    tab._slot_edited()
    assert spec.logx_mode == "linear"
    assert (spec.range_mode, spec.ymin, spec.xmax) == ("manual", 0.0, None)
    assert tab.ed_range["xmax"].text() == ""
    tab.ed_range["ymin"].setText("")
    tab._slot_edited()
    assert spec.range_mode == "auto"
    tab.deleteLater()


# ── 3 흔적 없이 지우기 · 드래그 네모 · 한 번에 되돌리기 ──────────
def test_excluded_points_leave_no_trace(qapp, demo_state):
    from etreport.ui.widgets.plot_canvas import PlotCanvas

    demo_state.excluded.clear()                  # 데모는 미리 뺀 점이 있다
    cv = PlotCanvas(demo_state)
    cv.on_pick = lambda _k: None                 # 그려지는 점의 key를 모으게
    cv.draw_spec(demo_state.explore)
    before = _drawn_points(cv.figure)
    demo_state.excluded.update(cv._series[0][2][:5])
    cv.draw_spec(demo_state.explore)
    assert _drawn_points(cv.figure) == before - 5
    cv.show_hidden = True                        # [클릭 → 복원] 모드만 보인다
    cv.draw_spec(demo_state.explore)
    assert _drawn_points(cv.figure) == before
    cv.deleteLater()


def test_drag_box_excludes_all_inside_and_undo_restores(qapp, demo_state):
    from etreport.model.state import StateBus
    from etreport.ui.tabs.explore import ExploreTab

    demo_state.excluded.clear()
    tab = ExploreTab(demo_state, StateBus())
    tab.redraw()
    cv = tab.canvas
    ax = cv.figure.axes[0]
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    a = ax.transData.transform((x0, y0))
    b = ax.transData.transform((x1, y1))
    cv._on_press(_Ev(*a))
    cv._on_release(_Ev(*b))                      # 축 전체를 감싼다
    n = len(demo_state.excluded)
    assert n > 10, "드래그 네모 안의 점이 빠지지 않았다"
    assert len(demo_state.undo_stack) == 1       # 네모 하나 = 되돌리기 한 칸
    where, keys = demo_state.undo_stack.pop()
    where.difference_update(keys)
    assert not demo_state.excluded
    tab.deleteLater()


# ── 4 이 plot에서만 제외 ─────────────────────────────────────
def test_unchecked_all_plots_excludes_only_that_plot(qapp, demo_state):
    from etreport.export.deckbuild import _plot_data
    from etreport.model.state import StateBus
    from etreport.ui.tabs.report import ReportTab

    demo_state.excluded.clear()
    tab = ReportTab(demo_state, StateBus())
    tab.chk_all.setChecked(False)
    a, b = PlotSpec(x="Vth N SVT", y="Idsat N SVT"), PlotSpec()
    key = demo_state.data["key"][0]
    tab._pick_point(key, a)
    assert key in a.local_excluded
    assert key not in b.local_excluded and key not in demo_state.excluded
    keys = {k for df in _plot_data(demo_state, "", a).values()
            for k in df["key"].to_list()}
    assert key not in keys                       # PPT도 같은 규칙
    tab.deleteLater()


# ── 5·8 그룹 스타일이 [적용]·PPT를 거쳐도 산다 ──────────────────
def test_merge_split_styles_keeps_user_edits(demo_state):
    from etreport.data.loader import merge_split_styles

    if demo_state.split is None or not demo_state.factors:
        pytest.skip("데모에 실험 조건이 없다")
    g = demo_state.groups[0]
    g.color, g.symbol, g.size, g.visible = "#123456", "s", 9, False
    demo_state.groups.append(GroupStyle(gid="m1", name="손"))
    demo_state.manual_groups[("L", "1", None, None, None)] = "m1"
    out = merge_split_styles(demo_state)
    same = next(x for x in out if x.gid == g.gid)
    assert (same.color, same.symbol, same.size, same.visible) == (
        "#123456", "s", 9, False)
    assert any(x.gid == "m1" for x in out)


def test_ppt_experiment_styles_follow_screen_colors(demo_state):
    from etreport.export.deckbuild import _styles_for

    if demo_state.split is None or not demo_state.factors:
        pytest.skip("데모에 실험 조건이 없다")
    exp = demo_state.factors[0]
    first = demo_state.split.styles_for([exp])[0]
    demo_state.groups = [GroupStyle(gid="z", name=first.name, color="#abcdef")]
    assert _styles_for(demo_state, exp)[0].color == "#abcdef"


# ── 9 막대는 평균 하나, 범위 없이 넓게 ─────────────────────────
def test_bar_has_no_error_bars_and_is_wide():
    import polars as pl

    from etreport.data.reformatter import Reformatter
    from etreport.render.mpl_renderer import render

    df = pl.DataFrame({"key": ["a", "b", "c", "d"], "lot": ["L"] * 4,
                       "wafer": ["1", "1", "2", "2"], "gid": ["g"] * 4,
                       "A": [1.0, 2.0, 3.0, 4.0]})
    fig = render(PlotSpec(type="bar", x="wafer", y="A"), {"g": df},
                 [GroupStyle(gid="g", name="g")], Reformatter(rules=[]),
                 [], (4, 3))
    ax = fig.axes[0]
    assert not any(type(c).__name__ == "ErrorbarContainer"
                   for c in ax.containers)
    assert ax.patches[0].get_width() > 0.7       # 그룹 하나면 자리를 다 쓴다


def test_wafer_mode_click_excludes_the_whole_wafer(qapp, demo_state):
    """점을 wafer 평균으로 찍어도 클릭 제외가 된다 — 점 하나 = 그 wafer 전부."""
    from etreport.model.state import StateBus
    from etreport.ui.tabs.explore import ExploreTab

    demo_state.excluded.clear()
    demo_state.explore.mode = "avg"
    tab = ExploreTab(demo_state, StateBus())
    tab.redraw()
    cv = tab.canvas
    xs, ys, keys = cv._series[0]
    assert isinstance(keys[0], list), "wafer 모드인데 측정점 좌표를 모았다"
    px = cv.figure.axes[0].transData.transform((xs[0], ys[0]))
    cv._on_press(_Ev(*px))
    cv._on_release(_Ev(*px))
    assert demo_state.excluded == set(keys[0])
    assert len(demo_state.undo_stack) == 1
    demo_state.explore.mode = "site"
    tab.deleteLater()


def test_hover_shows_lot_and_wafer_without_collecting_at_draw(qapp, demo_state):
    """마우스를 올리면 그 점의 lot·wafer — 좌표는 그릴 때가 아니라 그때 모은다."""
    from etreport.ui.widgets.plot_canvas import PlotCanvas

    cv = PlotCanvas(demo_state)                  # 클릭 제외 없음 = 리포트 미리보기
    cv.resize(500, 400)
    cv.draw_spec(demo_state.explore)
    assert cv._series == []                      # 그리기 비용은 그대로
    cv._collect_points(demo_state.explore, force=True)
    xs, ys, _keys = cv._series[0]
    px = cv.figure.axes[0].transData.transform((xs[0], ys[0]))
    key, _x, _y = cv._nearest(*px)
    text = cv.hover_text(key, _x, _y)
    row = demo_state.data.filter(demo_state.data["key"] == key).row(0, named=True)
    assert f"lot  {row['lot']}" in text and f"wafer  {row['wafer']}" in text
    cv.deleteLater()


def test_inline_and_tracking_columns_reach_plots_and_ppt(qapp, demo_state):
    """inline 계측(숫자) = 산점도 축, fab tracking(문자열) = boxplot 범주 —
    화면·리포트 슬롯 후보·PPT 데이터까지 같은 열로 간다(요청 2026-10-14 §5)."""
    from etreport import demo_data
    from etreport.data import fabtracking as ft
    from etreport.data import metrology as mt
    from etreport.export.deckbuild import _plot_data
    from etreport.model import categories as cat
    from etreport.model.state import StateBus
    from etreport.ui.tabs.report import ReportTab
    from etreport.ui.widgets.plot_canvas import PlotCanvas

    st = demo_state
    lots = sorted(set(st.data["lot"].to_list()))
    st.data, met = mt.attach(st.data, demo_data.metrology_frame(lots), "wafer")
    st.met_columns = met
    tf = demo_data.tracking_frame(lots)
    st.data, trk = ft.attach(st.data, ft.derive(tf, ft.suggest_columns(tf)))
    st.track_columns = trk
    assert met and trk

    y = st.aliases()[0]
    scatter = PlotSpec(x=met[0], y=y, type="scatter", mode="avg")
    box = PlotSpec(x=trk[0], y=y, type="box")
    assert met[0] in st.value_columns()
    assert trk[0] in cat.choices(st.data, st.track_columns + st.met_columns)
    tab = ReportTab(st, StateBus())
    assert met[0] in tab._alias_items()          # 리포트 슬롯 축 후보에도
    tab.deleteLater()
    for spec in (scatter, box):
        cv = PlotCanvas(st)
        cv.draw_spec(spec)
        assert _drawn_points(cv.figure) or cv.figure.axes[0].patches \
            or cv.figure.axes[0].lines, f"{spec.type}: 아무것도 그려지지 않았다"
        cv.deleteLater()
        data = _plot_data(st, "", spec)
        assert any(spec.x in d.columns and d[spec.x].drop_nulls().len()
                   for d in data.values()), f"PPT 데이터에 {spec.x}가 없다"


# ── 그룹 체크를 끄면 자동 축이 남은 그룹에 맞춰진다 ─────────────
def test_hidden_group_leaves_autoscale():
    import polars as pl

    from etreport.data.reformatter import Reformatter
    from etreport.render.mpl_renderer import render

    near = pl.DataFrame({"key": ["a", "b"], "gid": ["g1"] * 2,
                         "A": [1.0, 2.0], "B": [1.0, 2.0]})
    far = pl.DataFrame({"key": ["c"], "gid": ["g2"], "A": [1000.0], "B": [1000.0]})
    styles = [GroupStyle(gid="g1", name="g1"),
              GroupStyle(gid="g2", name="g2", visible=False)]
    fig = render(PlotSpec(x="A", y="B"), {"g1": near, "g2": far}, styles,
                 Reformatter(rules=[]), [], (4, 3))
    ax = fig.axes[0]
    assert ax.get_xlim()[1] < 10 and ax.get_ylim()[1] < 10
