"""플롯 캔버스 — mpl_renderer를 그대로 화면에 띄운다.

화면(pyqtgraph)과 PPT(matplotlib)를 따로 두면 "화면=PPT"를 매번 검증해야
한다. 렌더러를 하나로 합치면 그 문제가 원천적으로 사라진다 — 대가는
줌·팬 같은 실시간 상호작용인데, 축 범위를 규칙으로 고정하기로 했으므로
잃는 게 거의 없다. 필요한 상호작용(점 클릭 제외)은 여기서 직접 처리한다.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
import polars as pl
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QSizePolicy, QToolTip

from etreport.model.specs import GroupStyle, PlotSpec
from etreport.model.state import AppState
from etreport.render import mpl_renderer

log = logging.getLogger(__name__)

#: 점 위에 멈춘 뒤 정보가 뜨기까지(ms)
HOVER_MS = 1000

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
        # wafer 집계 모드에서는 점마다 key가 아니라 그 wafer의 key 목록이다.
        self._series: list[tuple[np.ndarray, np.ndarray, list]] = []
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
        # 마우스를 멈추고 HOVER_MS가 지나면 가장 가까운 점의 lot·wafer를 띄운다.
        # 움직일 때마다 타이머만 다시 거므로 그리기·이동 비용은 그대로다.
        self._hover_at: tuple[float, float] | None = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(HOVER_MS)
        self._hover_timer.timeout.connect(self._show_hover)

    # ── 그리기 ───────────────────────────────────────────────
    def render_args(self, spec: PlotSpec) -> tuple | None:
        """렌더러에 넘길 인자 묶음 — 데이터 준비와 렌더를 나눠 둔다."""
        from etreport.data.loader import with_values
        st = self.state
        if st.data is None or not spec.pairs():
            return None
        styles = st.groups or [_ALL]
        names = [n for pair in spec.pairs() for n in pair]
        active = st.active()
        if spec.local_excluded:          # 이 plot에서만 뺀 점(요청 §4)
            active = active.filter(
                ~pl.col("key").is_in(list(spec.local_excluded)))
        # 키 프레임에 **이 plot의 item 열만** 붙인다(작업 세트) — 슬롯 6개가 각자
        # item 전부를 들고 있지 않게. 그룹 분할도 그 좁은 프레임에서 한다.
        active = with_values(st, active, names)
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
                with_values(st, self._excluded_frame(), names), self.mini,
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
        # [적용] 때 안 읽은 item이면 그 열만 더 읽는다(화면과 PPT가 같은 함수)
        from etreport.data.loader import ensure_items
        ensure_items(self.state, [n for pair in spec.pairs() for n in pair])
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

    def _collect_points(self, spec: PlotSpec, force: bool = False) -> None:
        """히트테스트용 데이터 좌표 수집 (픽셀 변환은 클릭 때).

        **클릭 제외가 꺼져 있으면 아무것도 모으지 않는다.** 예전에는 `on_pick`
        여부와 무관하게 돌아서, 리포트 미리보기 한 번에 `key.to_list()`가 슬롯
        수만큼(6번) 실행됐다 — 20만 행이면 파이썬 문자열 객체 120만 개를
        만들었다가 버리는 셈이었다.
        """
        if self.on_pick is None and self.on_box is None and not force:
            self._series, self._series_key = [], None
            return
        if spec.type != "scatter":
            self._series, self._series_key = [], None
            return
        st = self.state
        if st.data is None:
            self._series, self._series_key = [], None
            return
        # 숨긴 그룹의 점은 찍히지 않아야 한다 — 보이지 않는 점이 지워지면 안 된다
        gids = ([g.gid for g in st.groups if g.visible] if st.groups else None)
        wafer = spec.mode != "site"
        # 같은 데이터·같은 축·같은 그룹이면 다시 모으지 않는다. 점을 하나 지울
        # 때마다 슬롯 6개가 20만 개의 key를 파이썬 문자열로 다시 만들던 자리다.
        # wafer 집계는 제외가 바뀌면 점 자리가 움직이므로 active도 키에 넣는다.
        cache = (id(st.data), tuple(spec.pairs()),
                 None if gids is None else tuple(gids), spec.mode,
                 id(st.active()) if wafer else None,
                 len(spec.local_excluded) if wafer else None, self.show_hidden)
        if cache == self._series_key:
            return
        self._series, self._series_key = [], cache
        from etreport.data.loader import with_values
        base = with_values(st, st.data, [n for pair in spec.pairs() for n in pair])
        if gids is not None and "gid" in base.columns:
            base = base.filter(pl.col("gid").is_in(gids))
        if wafer:
            self._collect_wafers(spec, base)
            if not self.show_hidden:
                return
            # [복원] 모드의 회색 점은 측정점 그대로 그려지므로 측정점으로 찍는다
            hide = st.hidden()
            base = base.filter(pl.col("key").is_in(list(hide)))
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

    def _collect_wafers(self, spec: PlotSpec, base: pl.DataFrame) -> None:
        """wafer 집계 점 — 점 하나 = (그룹, lot, wafer), 찍으면 그 wafer의 측정점 전부.

        렌더러(`_scatter_aggregate` → `wafer_stats`)와 같은 행·같은 집계식으로
        자리를 잡는다. 그림에 없는 점(제외·이 plot만 뺀 점)은 집계에서 빠진다.
        """
        from etreport.model.aggregate import _agg_expr
        drop = self.state.hidden() | set(spec.local_excluded)
        if drop:
            base = base.filter(~pl.col("key").is_in(list(drop)))
        by = [c for c in ("gid", "lot", "wafer") if c in base.columns]
        for ax_x, ax_y in spec.pairs():
            if ax_x not in base.columns or ax_y not in base.columns:
                continue
            ys = "__y" if ax_y == ax_x else ax_y
            g = (base.group_by(by)
                 .agg(_agg_expr(spec.mode, ax_x).alias(ax_x),
                      _agg_expr(spec.mode, ax_y).alias(ys), pl.col("key"))
                 .drop_nulls([ax_x, ys]))
            if not g.is_empty():
                self._series.append((g[ax_x].to_numpy(), g[ys].to_numpy(),
                                     g["key"].to_list()))

    def _emit(self, keys: list[str]) -> None:
        if self.on_box is not None:
            self.on_box(keys)
        else:
            for k in keys:
                self.on_pick(k)

    # ── 클릭·드래그 → 제외/복원 ──────────────────────────────
    def _on_press(self, ev) -> None:
        self._press = None
        self._hover_timer.stop()
        if self.on_pick is None or ev.x is None or not self._series:
            return
        if getattr(ev, "button", 1) not in (1, None):
            return
        self._press = (ev.x, ev.y)

    def _on_motion(self, ev) -> None:
        if self._press is None:
            QToolTip.hideText()
            self._hover_at = None if ev.x is None else (ev.x, ev.y)
            if self._hover_at is not None and self.spec is not None:
                self._hover_timer.start()
            return
        if ev.x is None:
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
        # 현장 재현용 — 드래그가 "안 먹을" 때 무엇을 봤는지 로그에 남긴다
        log.info("드래그 제외: mode=%s 점 묶음=%s → 측정점 %d개",
                 getattr(self.spec, "mode", "?"),
                 [len(xs) for xs, _y, _k in self._series], len(keys))
        if keys:
            self._emit(keys)

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
                # wafer 집계 점은 측정점 key 목록을 들고 있다
                for k in (keys[i] if isinstance(keys[i], list) else [keys[i]]):
                    out[k] = None
        return list(out)

    def _nearest(self, x: float, y: float):
        """픽셀 (x, y)에서 `tol` 안의 가장 가까운 점 — `(key, 데이터 x, 데이터 y)`."""
        if not self._series or not self.figure.axes:
            return None
        ax = self.figure.axes[0]
        tol = 10.0 if self.mini else 15.0        # 슬롯은 작으므로 더 촘촘히

        # **역변환 한 번**으로 클릭 자리와 허용 오차를 데이터 공간에 옮긴다.
        # 예전에는 점 전체를 순방향으로 픽셀 변환했다 — 20만 점이면 클릭할
        # 때마다 N×2 배열을 만들어 아핀 변환을 돌렸다. 로그 축에서도 맞도록
        # 오차는 좌우·상하 tol만큼 떨어진 자리를 각각 역변환해 구한다.
        inv = ax.transData.inverted()
        lo_x, lo_y = inv.transform((x - tol, y - tol))
        hi_x, hi_y = inv.transform((x + tol, y + tol))
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
            d = (pix[:, 0] - x) ** 2 + (pix[:, 1] - y) ** 2
            i = int(np.argmin(d))
            if d[i] < bd:
                j = idx[i]
                bd, best = d[i], (keys[j], float(xs[j]), float(ys[j]))
        return best

    def _click(self, ev) -> None:
        if self.on_pick is None or ev.x is None:
            return
        hit = self._nearest(ev.x, ev.y)
        if hit is None:
            return
        if isinstance(hit[0], list):          # wafer 집계 점 — 그 wafer 전부
            self._emit(hit[0])
        else:
            self.on_pick(hit[0])

    # ── 마우스를 올리면 점 정보 ──────────────────────────────
    def _show_hover(self) -> None:
        """멈춘 자리의 점 하나 — lot·wafer·그룹·조건과 x·y 값을 툴팁으로.

        좌표는 이때 처음 모은다(`force`) — 그리기마다 모으면 슬롯 6개가 매번
        key 목록을 만든다(비용 규약). 같은 그림이면 캐시가 그대로 쓰인다.
        """
        spec, at = self.spec, self._hover_at
        if spec is None or at is None or not self.isVisible():
            return
        self._collect_points(spec, force=True)
        hit = self._nearest(*at)
        if hit is None:
            return
        text = self.hover_text(*hit)
        if text:
            h = self.height()
            QToolTip.showText(self.mapToGlobal(QPoint(int(at[0]), int(h - at[1]))),
                              text, self)

    def hover_text(self, key, x: float, y: float) -> str:
        """툴팁 글자 — 측정점이면 그 점의 키, wafer 집계 점이면 wafer와 점 수."""
        from etreport.model.specs import fmt_value
        st = self.state
        keys = key if isinstance(key, list) else [key]
        if st.data is None or not keys:
            return ""
        row = st.data.filter(pl.col("key") == keys[0]).head(1)
        if row.is_empty():
            return ""
        r = row.row(0, named=True)
        grp = st.group(r.get("gid") or "")
        lines = [f"lot  {r.get('lot')}", f"wafer  {r.get('wafer')}"]
        if grp is not None:
            lines.append(f"그룹  {grp.name}")
        if isinstance(key, list):
            lines.append(f"측정점  {len(keys)}개 (wafer {self._mode_name()})")
        else:
            cond = " · ".join(f"{k} {r[k]}" for k in ("step", "temp", "site")
                              if r.get(k) is not None)
            if cond:
                lines.append(cond)
        pairs = self.spec.pairs() if self.spec is not None else []
        if pairs:
            ax_x, ax_y = pairs[0]
            lines += [f"{ax_x}  {fmt_value(x)}", f"{ax_y}  {fmt_value(y)}"]
        return "\n".join(lines)

    def _mode_name(self) -> str:
        return {"avg": "평균", "med": "중앙값", "std": "표준편차"}.get(
            getattr(self.spec, "mode", ""), "집계")

    def leaveEvent(self, event) -> None:
        self._hover_timer.stop()
        self._hover_at = None
        super().leaveEvent(event)
