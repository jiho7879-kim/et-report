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
from PySide6.QtCore import QTimer
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
        # matplotlib 캔버스는 figure 크기를 최소 크기로 제안한다. 슬롯(2×3)은
        # 그보다 작을 수 있어 그대로 두면 캔버스가 슬롯 밖으로 삐져나와
        # 축 이름이 아래 슬롯 위에 겹쳐 그려진다 — 작게 줄어들 수 있게 한다.
        self.setMinimumSize(80, 60)
        self.setParent(parent)
        self.mpl_connect("button_press_event", self._click)
        # 크기가 바뀌면 여백(tight_layout)이 어긋나 축 이름이 밖으로 삐져나온다.
        # 리사이즈가 멎은 뒤 한 번만 다시 그린다.
        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(80)
        self._redraw_timer.timeout.connect(self._redraw_current)

    # ── 그리기 ───────────────────────────────────────────────
    def render_args(self, spec: PlotSpec) -> tuple | None:
        """렌더러에 넘길 인자 묶음 — 데이터 준비와 렌더를 나눠 둔다."""
        st = self.state
        if st.data is None or not spec.pairs():
            return None
        styles = st.groups or [_ALL]
        active = st.active()
        data = {g.gid: (active if g.gid == "" and not st.groups
                        else active.filter(pl.col("gid") == g.gid))
                for g in styles}
        w, h = self.figure.get_size_inches()
        return (spec, data, styles, st.rf, st.log_patterns, (w, h),
                self._excluded_frame(), self.mini,
                bool(getattr(st, "lot_split_symbols", False)))

    def _sync_size(self) -> None:
        """Figure 크기를 **위젯 실제 크기**에 맞춘다.

        맞추지 않으면 그림이 위젯보다 크게 그려져 축 이름이 슬롯 밖으로
        삐져나오고(아래 슬롯 위에 겹쳐 보인다) y축 눈금이 잘린다.
        """
        dpi = self.figure.get_dpi() or 100
        w = max(1.2, self.width() / dpi)
        h = max(1.0, self.height() / dpi)
        if abs(w - self.figure.get_figwidth()) > 0.01 or \
                abs(h - self.figure.get_figheight()) > 0.01:
            self.figure.set_size_inches(w, h, forward=False)

    def draw_spec(self, spec: PlotSpec) -> None:
        self.spec = spec
        self._sync_size()
        self.figure.clear()
        args = self.render_args(spec)
        if args is None:
            self.draw_idle()
            return
        s, data, styles, rf, patterns, size, excluded, compact, lot_split = args
        mpl_renderer.render(s, data, styles, rf, patterns, size,
                            excluded=excluded, compact=compact,
                            fig=self.figure,      # 캔버스 figure에 직접
                            lot_split=lot_split)
        self._collect_points(spec)
        self.draw_idle()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.spec is not None:
            self._redraw_timer.start()

    def _redraw_current(self) -> None:
        if self.spec is None:
            return
        self.draw_spec(self.spec)
        self.draw()          # 즉시 다시 칠한다 — 예전 라벨 잔상이 남지 않게

    def _excluded_frame(self) -> pl.DataFrame | None:
        """회색 빈 심볼로 남길 점 — 손으로 찍은 제외 **과 이상치 필터**.

        필터가 걸러 낸 점도 그림에 남긴다. 계산에서는 빠지되 화면에서 통째로
        사라지면 "왜 이 점이 없지"를 확인할 방법이 없다.
        """
        st = self.state
        hide = st.hidden()
        if st.data is None or not hide:
            return None
        return st.data.filter(pl.col("key").is_in(list(hide)))

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
            # x·y가 같은 item일 수 있다 — 중복 열을 그대로 select하면
            # polars가 DuplicateError를 낸다(§10.10)
            cols = list(dict.fromkeys(["key", ax_x, ax_y]))
            sub = st.data.select(cols).drop_nulls()
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
