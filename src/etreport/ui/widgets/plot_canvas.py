"""플롯 캔버스 — mpl_renderer를 그대로 화면에 띄운다.

화면(pyqtgraph)과 PPT(matplotlib)를 따로 두면 "화면=PPT"를 매번 검증해야
한다. 렌더러를 하나로 합치면 그 문제가 원천적으로 사라진다 — 대가는
줌·팬 같은 실시간 상호작용인데, 축 범위를 규칙으로 고정하기로 했으므로
잃는 게 거의 없다. 필요한 상호작용(점 클릭 제외)은 여기서 직접 처리한다.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import polars as pl
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QSizePolicy

from etreport.model.specs import GroupStyle, PlotSpec
from etreport.model.state import AppState
from etreport.render import mpl_renderer

_ALL = GroupStyle(gid="", name="전체", color="#0071e3", symbol="o", size=6)


class PlotCanvas(FigureCanvasQTAgg):
    def __init__(self, state: AppState, mini: bool = False, parent=None) -> None:
        self.state = state
        self.mini = mini
        self.spec: PlotSpec | None = None
        self.on_pick: Callable[[str], None] | None = None
        # 히트테스트용 원본 좌표. **픽셀로 미리 변환해 두지 않는다** —
        # 슬롯 캔버스는 그린 뒤 레이아웃에서 크기가 바뀌므로 미리 캐시하면
        # 좌표가 어긋나 클릭이 먹지 않는다. 클릭 시점에 변환한다.
        self._series: list[tuple[np.ndarray, np.ndarray, list[str]]] = []
        super().__init__(Figure(figsize=(4, 3), dpi=100))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setParent(parent)
        self.mpl_connect("button_press_event", self._click)

    # ── 그리기 ───────────────────────────────────────────────
    def draw_spec(self, spec: PlotSpec) -> None:
        self.spec = spec
        self.figure.clear()
        st = self.state
        if st.data is None or not spec.pairs():
            self.draw_idle()
            return

        styles = st.groups or [_ALL]
        active = st.active()
        data = {g.gid: (active if g.gid == "" and not st.groups
                        else active.filter(pl.col("gid") == g.gid))
                for g in styles}
        w, h = self.figure.get_size_inches()
        mpl_renderer.render(spec, data, styles, st.rf,
                            st.log_patterns, (w, h),
                            excluded=self._excluded_frame(),
                            compact=self.mini,
                            fig=self.figure)      # 캔버스 figure에 직접
        self._collect_points(spec)
        self.draw_idle()

    def _excluded_frame(self) -> pl.DataFrame | None:
        st = self.state
        if st.data is None or not st.excluded:
            return None
        return st.data.filter(pl.col("key").is_in(list(st.excluded)))

    def _collect_points(self, spec: PlotSpec) -> None:
        """히트테스트용 데이터 좌표 수집 (픽셀 변환은 클릭 때)."""
        self._series = []
        if spec.type != "scatter" or spec.mode != "site":
            return
        st = self.state
        if st.data is None:
            return
        for ax_x, ax_y in spec.pairs():
            if ax_x not in st.data.columns or ax_y not in st.data.columns:
                continue
            sub = st.data.select(["key", ax_x, ax_y]).drop_nulls()
            if sub.is_empty():
                continue
            self._series.append((sub[ax_x].to_numpy(), sub[ax_y].to_numpy(),
                                 sub["key"].to_list()))

    # ── 클릭 → 제외/복원 ─────────────────────────────────────
    def _click(self, ev) -> None:
        if self.on_pick is None or ev.x is None or not self._series:
            return
        if not self.figure.axes:
            return
        ax = self.figure.axes[0]
        tol = 10.0 if self.mini else 15.0        # 슬롯은 작으므로 더 촘촘히
        best, bd = None, tol ** 2
        for xs, ys, keys in self._series:
            pix = ax.transData.transform(np.column_stack([xs, ys]))
            d = (pix[:, 0] - ev.x) ** 2 + (pix[:, 1] - ev.y) ** 2
            i = int(np.argmin(d))
            if d[i] < bd:
                bd, best = d[i], keys[i]
        if best:
            self.on_pick(best)
