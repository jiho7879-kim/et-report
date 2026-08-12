"""데모에서 W,L trend가 기하(WIDTH/LENGTH) 값으로 렌더되는지 검증."""
from __future__ import annotations

from etreport.demo import load_demo
from etreport.model.state import AppState
from etreport.render import mpl_renderer


def _demo_state() -> AppState:
    st = AppState()
    load_demo(st)
    return st


def test_demo_reformer_carries_geometry():
    st = _demo_state()
    # WIDTH/LENGTH가 Rule.w/Rule.l로 들어가는지
    assert st.rf.by_alias["Idsat N SVT"].w == 0.20
    assert st.rf.by_alias["Idsat P SVT"].l == 0.04
    # MIM 캡 등 기하가 없는 항목은 None
    assert st.rf.by_alias["Cap MIM unit"].w is None


def test_demo_report_has_trend_page():
    st = _demo_state()
    trends = [s for p in st.report.pages for s in p.slots
              if s is not None and s.type == "trend"]
    assert len(trends) >= 2


def test_demo_trend_renders_with_geometry():
    st = _demo_state()
    spec = next(s for p in st.report.pages for s in p.slots
                if s is not None and s.type == "trend")
    fig = mpl_renderer.render(spec, {"": st.data}, st.groups, st.rf, [], (4, 3))
    ax = fig.axes[0]
    # 대표값 라인 또는 점 스트립 중 하나는 그려져야 함
    assert ax.lines or ax.collections
