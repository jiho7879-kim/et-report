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
        # 드래그로 그린 네모 안의 점 전부 — 없으면 점마다 on_pick을 부른다
        self.on_box: Callable[[list[str]], None] | None = None
        # 제외한 점을 회색 빈 심볼로 보일지. 평소에는 **흔적 없이** 지우고,
        # 탐색 탭의 [클릭 → 복원] 모드에서만 켠다(찍을 자리가 보여야 하므로).
        self.show_hidden = False
        self._press: tuple[float, float] | None = None
        self._series_key: tuple | None = None
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
        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("motion_notify_event", self._on_motion)
        self.mpl_connect("button_release_event", self._on_release)
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
        if spec.local_excluded:          # 이 plot에서만 뺀 점(요청 §4)
            active = active.filter(
                ~pl.col("key").is_in(list(spec.local_excluded)))
        if not st.groups:
            data = {"": active}
        else:
            # `partition_by`로 **한 번에** 쪼갠다. 그룹마다 filter를 걸면 전체
            # 프레임을 그룹 수만큼 훑어 O(n×g)가 된다(슬롯 6개 × 그룹 4개면
            # 20만 행을 24번). 값이 없는 그룹도 자리를 만들어 둔다 —
            # 렌더러가 `st.gid not in data`로 건너뛰기 때문이다.
            parts = (active.partition_by("gid", as_dict=True)
                     if "gid" in active.columns else {})
            data = {}
            for g in styles:
                sub = parts.get((g.gid,), parts.get(g.gid))
                if sub is not None:
                    data[g.gid] = sub
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
        """회색 빈 심볼로 남길 점 — `show_hidden`일 때만.

        예전에는 늘 남겼는데, 지운 점이 테두리만 남은 채 그대로 보여서 "지워도
        지워지지 않는다"가 됐다. PPT도 넘기지 않으므로 평소 화면 = PPT다.
        만드는 일은 `AppState`가 한다(캐시는 거기 있다).
        """
        return self.state.hidden_frame() if self.show_hidden else None

    def _collect_points(self, spec: PlotSpec) -> None:
        """히트테스트용 데이터 좌표 수집 (픽셀 변환은 클릭 때).

        **클릭 제외가 꺼져 있으면 아무것도 모으지 않는다.** 예전에는 `on_pick`
        여부와 무관하게 돌아서, 리포트 미리보기 한 번에 `key.to_list()`가 슬롯
        수만큼(6번) 실행됐다 — 20만 행이면 파이썬 문자열 객체 120만 개를
        만들었다가 버리는 셈이었다.
        """
        if self.on_pick is None and self.on_box is None:
            self._series, self._series_key = [], None
            return
        if spec.type != "scatter" or spec.mode != "site":
            self._series, self._series_key = [], None
            return
        st = self.state
        if st.data is None:
            self._series, self._series_key = [], None
            return
        # 숨긴 그룹의 점은 찍히지 않아야 한다 — 보이지 않는 점이 지워지면 안 된다
        gids = ([g.gid for g in st.groups if g.visible] if st.groups else None)
        # 같은 데이터·같은 축·같은 그룹이면 다시 모으지 않는다. 점을 하나 지울
        # 때마다 슬롯 6개가 20만 개의 key를 파이썬 문자열로 다시 만들던 자리다.
        cache = (id(st.data), tuple(spec.pairs()),
                 None if gids is None else tuple(gids))
        if cache == self._series_key:
            return
        self._series, self._series_key = [], cache
        base = st.data
        if gids is not None and "gid" in base.columns:
            base = base.filter(pl.col("gid").is_in(gids))
        for ax_x, ax_y in spec.pairs():
            if ax_x not in base.columns or ax_y not in base.columns:
                continue
            # x·y가 같은 item일 수 있다 — 중복 열을 그대로 select하면
            # polars가 DuplicateError를 낸다(§10.10)
            cols = list(dict.fromkeys(["key", ax_x, ax_y]))
            sub = base.select(cols).drop_nulls()
            if sub.is_empty():
                continue
            self._series.append((sub[ax_x].to_numpy(), sub[ax_y].to_numpy(),
                                 sub["key"].to_list()))

    # ── 클릭·드래그 → 제외/복원 ──────────────────────────────
    def _on_press(self, ev) -> None:
        self._press = None
        if self.on_pick is None or ev.x is None or not self._series:
            return
        if getattr(ev, "button", 1) not in (1, None):
            return
        self._press = (ev.x, ev.y)

    def _on_motion(self, ev) -> None:
        if self._press is None or ev.x is None:
            return
        x0, y0 = self._press
        # 고무줄 네모 — Qt 좌표는 위가 0이라 y를 뒤집는다
        h = self.figure.bbox.height
        self.drawRectangle([x0, h - y0, ev.x - x0, y0 - ev.y])

    def _on_release(self, ev) -> None:
        press, self._press = self._press, None
        if press is None:
            return
        self.drawRectangle(None)
        if ev.x is None or max(abs(ev.x - press[0]), abs(ev.y - press[1])) < 5:
            self._click(ev)               # 거의 안 움직였으면 클릭이다
            return
        keys = self._keys_in(press, (ev.x, ev.y))
        if not keys:
            return
        if self.on_box is not None:
            self.on_box(keys)
        else:
            for k in keys:
                self.on_pick(k)

    def _keys_in(self, a, b) -> list[str]:
        """드래그한 네모 안의 점 — 모서리 두 개만 역변환해 데이터 공간에서 고른다."""
        if not self.figure.axes:
            return []
        inv = self.figure.axes[0].transData.inverted()
        (ax0, ay0), (ax1, ay1) = inv.transform([a, b])
        x0, x1 = sorted((ax0, ax1))
        y0, y1 = sorted((ay0, ay1))
        out: dict[str, None] = {}
        for xs, ys, keys in self._series:
            for i in np.flatnonzero((xs >= x0) & (xs <= x1)
                                    & (ys >= y0) & (ys <= y1)):
                out[keys[i]] = None
        return list(out)

    def _click(self, ev) -> None:
        if self.on_pick is None or ev.x is None or not self._series:
            return
        if not self.figure.axes:
            return
        ax = self.figure.axes[0]
        tol = 10.0 if self.mini else 15.0        # 슬롯은 작으므로 더 촘촘히

        # **역변환 한 번**으로 클릭 자리와 허용 오차를 데이터 공간에 옮긴다.
        # 예전에는 점 전체를 순방향으로 픽셀 변환했다 — 20만 점이면 클릭할
        # 때마다 N×2 배열을 만들어 아핀 변환을 돌렸다. 로그 축에서도 맞도록
        # 오차는 좌우·상하 tol만큼 떨어진 자리를 각각 역변환해 구한다.
        inv = ax.transData.inverted()
        lo_x, lo_y = inv.transform((ev.x - tol, ev.y - tol))
        hi_x, hi_y = inv.transform((ev.x + tol, ev.y + tol))
        x0, x1 = min(lo_x, hi_x), max(lo_x, hi_x)
        y0, y1 = min(lo_y, hi_y), max(lo_y, hi_y)

        best, bd = None, tol ** 2
        for xs, ys, keys in self._series:
            near = (xs >= x0) & (xs <= x1) & (ys >= y0) & (ys <= y1)
            idx = np.flatnonzero(near)
            if idx.size == 0:
                continue
            # 후보만 정확히 픽셀 거리로 재 본다(둥근 허용 반경 · 축 비율 무시 없이)
            pix = ax.transData.transform(
                np.column_stack([xs[idx], ys[idx]]))
            d = (pix[:, 0] - ev.x) ** 2 + (pix[:, 1] - ev.y) ** 2
            i = int(np.argmin(d))
            if d[i] < bd:
                bd, best = d[i], keys[idx[i]]
        if best:
            self.on_pick(best)
