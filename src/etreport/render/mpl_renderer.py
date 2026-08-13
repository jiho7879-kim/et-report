"""matplotlib 렌더러 — PlotSpec 하나 → Figure 하나.

화면 캔버스(ui/widgets/plot_canvas.py)와 PPT가 **이 함수 하나**를 공유한다.
축 규칙은 ranges.py, 심볼 매핑은 아래 표가 단일 진실이다.
배경은 흰색 — PPT에 그대로 들어간다.

pyplot은 쓰지 않는다(Figure를 직접 생성) — 전역 매니저에 쌓이지 않게.
"""
from __future__ import annotations

import logging

import matplotlib

matplotlib.use("Agg")
import polars as pl
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator

from etreport import fonts
from etreport.data.reformatter import Reformatter
from etreport.model.aggregate import group_representatives, wafer_stats
from etreport.model.specs import GroupStyle, PlotSpec
from etreport.render.ranges import compute_range, resolve_axes, resolve_log

log = logging.getLogger(__name__)

# pyqtgraph 심볼 ↔ matplotlib 마커 (한 곳에서만 정의)
MARKER = {"o": "o", "s": "s", "t": "^", "d": "D", "+": "+"}
SPEC_COLOR = "#d70015"      # 규격 — 빨간 실선
TARGET_COLOR = "#0071e3"    # 타깃 — 파란 X
REF_COLOR = "#8e8e93"       # REF 그룹 라인 — 회색
FONT_MIN_PT = 8            # 8pt 하한 — PPT에서 읽히는 최소 크기
FONT_MAX_PT = 13.0         # 축 이름·제목 상한
FONT_SCALE = 3.0           # figsize(인치)당 글자 크기 — 클수록 크게 나온다


def _font_size(figsize: tuple[float, float], compact: bool) -> float:
    """축 이름·눈금·제목 글자 크기.

    PPT 슬롯(2×3)은 그림이 작아 예전 값(≈9.5pt 상한, 인치당 2.2)으로는 축
    숫자가 안 읽혔다. 상한과 배율을 함께 올린다 — 화면 캔버스도 같은 함수를
    쓰므로 "화면 = PPT"는 그대로다.
    """
    if compact:
        # 화면 슬롯(2×3)은 그림이 손바닥만 해서 크게 하면 축 이름·눈금이 잘린다.
        # PPT는 compact=False로 그리므로 여기 상한은 PPT 글자에 영향이 없다.
        return max(FONT_MIN_PT - 2, min(8.0, figsize[0] * 2.4))
    return max(FONT_MIN_PT, min(FONT_MAX_PT, figsize[0] * FONT_SCALE))

# 한글 축 이름·제목이 □로 깨지지 않도록 OS별 한글 폰트를 잡는다(fonts.py 참조).
fonts.setup_matplotlib()


def render(spec: PlotSpec,
           data: dict[str, pl.DataFrame],      # gid → (x, y, alias별 값 wide)
           styles: list[GroupStyle],
           rf: Reformatter,
           log_patterns: list[str],
           figsize: tuple[float, float],
           excluded: pl.DataFrame | None = None,
           compact: bool = False,
           fig: Figure | None = None) -> Figure:
    """compact=True면 슬롯/미니용 — 라벨을 줄이고 여백을 좁힌다.

    fig를 주면 그 Figure에 그린다(화면 캔버스용). 안 주면 새로 만든다(PPT용).

    새로 만들 때 pyplot을 쓰지 않는다 — pyplot은 만든 Figure를
    전역 매니저에 등록해 두기 때문에, 명시적으로 닫지 않으면 덱 하나를 만들 때
    생긴 수백 개의 Figure가 프로세스가 끝날 때까지 메모리에 남는다.
    """
    if spec.type == "trend":
        return _render_trend(spec, data, styles, rf, log_patterns, figsize,
                             excluded=excluded, compact=compact, fig=fig)
    if fig is None:
        fig = Figure(figsize=figsize, dpi=140 if compact else 180)
        ax = fig.add_subplot(111)
    else:
        fig.clear()
        ax = fig.add_subplot(111)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    pairs = spec.pairs()
    # 데이터 min/max 수집 (축 규칙 입력)
    dr: dict[str, tuple[float, float]] = {}
    for df in data.values():
        for a in {p for pr in pairs for p in pr}:
            if a in df.columns and df[a].drop_nulls().len():
                lo, hi = float(df[a].min()), float(df[a].max())
                cur = dr.get(a)
                dr[a] = (min(lo, cur[0]) if cur else lo,
                         max(hi, cur[1]) if cur else hi)

    (xlo, xhi, lgx), (ylo, yhi, lgy) = resolve_axes(spec, rf, log_patterns, dr)

    # 포인트 — 모든 xy쌍이 그룹 스타일을 공유(확정 사양)
    excluded_keys = (set(excluded["key"]) if excluded is not None
                     and not excluded.is_empty() else set())
    for st in styles:
        if not st.visible or st.gid not in data:
            continue
        df = data[st.gid]
        if spec.mode != "site":
            _scatter_aggregate(ax, df, pairs, st, spec.mode, excluded_keys)
            continue
        for ax_x, ax_y in pairs:
            if ax_x not in df.columns or ax_y not in df.columns:
                continue
            ax.scatter(df[ax_x], df[ax_y],
                       s=st.size ** 2, c=st.color,
                       marker=MARKER.get(st.symbol, "o"),
                       linewidths=0, alpha=0.9, zorder=3,
                       label=st.name if (ax_x, ax_y) == pairs[0] else None)

    # 제외된 포인트 — 회색 빈 심볼로 남긴다(사라지지 않게)
    if excluded is not None and not excluded.is_empty():
        for ax_x, ax_y in pairs:
            if ax_x in excluded.columns and ax_y in excluded.columns:
                ax.scatter(excluded[ax_x], excluded[ax_y], s=26,
                           facecolors="none", edgecolors="#c7c7cc",
                           linewidths=0.9, zorder=2.5)

    # 규격 — 십자로 삐져나온 선 대신 **규격 창(박스)**을 빨간 실선으로.
    # 한쪽 규격이 없으면 그 변은 축 끝까지 열어 둔다(합집합, 확정 사양).
    def _bounds(aliases: set[str]) -> tuple[float | None, float | None]:
        lo = [rf.by_alias[a].speclow for a in aliases
              if a in rf.by_alias and rf.by_alias[a].speclow is not None]
        hi = [rf.by_alias[a].spechigh for a in aliases
              if a in rf.by_alias and rf.by_alias[a].spechigh is not None]
        return (min(lo) if lo else None, max(hi) if hi else None)

    x_lo, x_hi = _bounds({p[0] for p in pairs})
    y_lo, y_hi = _bounds({p[1] for p in pairs})
    _spec_box(ax, x_lo, x_hi, y_lo, y_hi)

    # 타깃 — 파란 X. 양축 모두 있으면 교점 하나, 한쪽만 있으면 그 축의 선.
    def _target(aliases: set[str]) -> float | None:
        ts = [rf.by_alias[a].target for a in aliases
              if a in rf.by_alias and rf.by_alias[a].target is not None]
        return sum(ts) / len(ts) if ts else None

    tx, ty = _target({p[0] for p in pairs}), _target({p[1] for p in pairs})
    if tx is not None and ty is not None:
        ax.plot([tx], [ty], marker="x", color=TARGET_COLOR, markersize=11,
                markeredgewidth=2.0, zorder=6, linestyle="none", label="_target")
    elif tx is not None:
        ax.axvline(tx, color=TARGET_COLOR, lw=1.1, zorder=2.2)
    elif ty is not None:
        ax.axhline(ty, color=TARGET_COLOR, lw=1.1, zorder=2.2)

    if lgx:
        ax.set_xscale("log")
    if lgy:
        ax.set_yscale("log")
    ax.set_xlim(xlo, xhi)
    ax.set_ylim(ylo, yhi)
    ax.set_box_aspect(1)          # plot 영역은 항상 정사각형
    _flush_spec_box(ax)

    def _axis_name(name: str, aliases: list[str]) -> str:
        if name:
            return name
        u = next((rf.by_alias[a].unit for a in aliases
                  if a in rf.by_alias and rf.by_alias[a].unit), "")
        return ", ".join(aliases) + (f" [{u}]" if u else "")

    fs = _font_size(figsize, compact)
    ax.set_xlabel(_axis_name(spec.x_name, [p[0] for p in pairs]), fontsize=fs)
    ax.set_ylabel(_axis_name(spec.y_name, [p[1] for p in pairs]), fontsize=fs)
    if not compact:
        ax.set_title(spec.title, fontsize=fs + 1, fontweight="bold", loc="left")
    ax.tick_params(labelsize=max(FONT_MIN_PT, fs - 0.8),
                   pad=1 if compact else 3, length=2 if compact else 3)
    if compact:
        ax.xaxis.set_major_locator(MaxNLocator(3))
        ax.yaxis.set_major_locator(MaxNLocator(3))
    ax.grid(True, color="#ececee", lw=0.6, zorder=0)
    for sp in ax.spines.values():
        sp.set_color("#d2d2d7")
    if not compact and ax.get_legend_handles_labels()[0]:
        leg = ax.legend(fontsize=fs - 0.5, frameon=True, framealpha=0.95,
                        loc="upper left", bbox_to_anchor=(1.015, 1.0),
                        borderaxespad=0, markerscale=0.85)
        leg.get_frame().set_edgecolor("#d2d2d7")
        leg.get_frame().set_linewidth(0.6)
    fig.tight_layout(pad=0.8 if compact else 0.9)
    return fig


def _scatter_aggregate(ax, df: pl.DataFrame, pairs, st: GroupStyle,
                       agg: str, excluded_keys: set[str]) -> None:
    """mode=avg/med/std scatter — (lot,wafer) 집계 점 하나씩."""
    aliases = [a for pr in pairs for a in pr]
    ws = wafer_stats(df, excluded_keys, aliases, agg)
    for ax_x, ax_y in pairs:
        xs, ys = [], []
        for vals in ws.values.values():
            x, y = vals.get(ax_x), vals.get(ax_y)
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
        if not xs:
            continue
        ax.scatter(xs, ys, s=st.size ** 2, c=st.color,
                   marker=MARKER.get(st.symbol, "o"),
                   linewidths=0, alpha=0.9, zorder=3,
                   label=st.name if (ax_x, ax_y) == pairs[0] else None)


def _render_trend(spec: PlotSpec,
                  data: dict[str, pl.DataFrame],
                  styles: list[GroupStyle],
                  rf: Reformatter,
                  log_patterns: list[str],
                  figsize: tuple[float, float],
                  excluded: pl.DataFrame | None = None,
                  compact: bool = False,
                  fig: Figure | None = None) -> Figure:
    """기하(W/L) trend — x=규격 기하값, y=item 값, 대표값 라인 + 점 스트립.

    scatter와 달리 X축이 데이터 컬럼이 아니라 리포메터의 기하값(W/L)이므로
    축 계산을 직접 한다. pyplot은 쓰지 않는다(scatter와 동일).
    """
    if fig is None:
        fig = Figure(figsize=figsize, dpi=140 if compact else 180)
        ax = fig.add_subplot(111)
    else:
        fig.clear()
        ax = fig.add_subplot(111)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    geom = spec.x.strip()
    items = [y for _, y in spec.pairs()]
    excluded_keys = (set(excluded["key"]) if excluded is not None
                     and not excluded.is_empty() else set())

    xpos: dict[str, float] = {}
    for it in items:
        rule = rf.by_alias.get(it)
        xv = (rule.w if geom == "W" else rule.l) if rule is not None else None
        if xv is None:
            log.warning("trend %s: %s의 %s 값이 없어 제외합니다", geom, it, geom)
            continue
        xpos[it] = float(xv)
    plotted = [it for it in items if it in xpos]

    xvals = [xpos[it] for it in plotted]
    xlo, xhi = compute_range([geom], min(xvals) if xvals else None,
                             max(xvals) if xvals else None, rf, False)
    y_min = y_max = None
    for it in plotted:
        for df in data.values():
            col = df[it].drop_nulls() if it in df.columns else None
            if col is None or col.is_empty():
                continue
            lo, hi = float(col.min()), float(col.max())
            y_min = lo if y_min is None else min(y_min, lo)
            y_max = hi if y_max is None else max(y_max, hi)
    lgy = resolve_log(spec.logy_mode, plotted, log_patterns)
    ylo, yhi = compute_range(plotted, y_min, y_max, rf, lgy)

    for st in styles:
        if not st.visible or st.gid not in data:
            continue
        df = data[st.gid]
        for it in plotted:
            if spec.mode == "site":
                if it not in df.columns:
                    continue
                ys = df[it].drop_nulls().to_list()
            else:
                ws = wafer_stats(df, excluded_keys, [it], spec.mode)
                ys = [v for vals in ws.values.values()
                      if (v := vals.get(it)) is not None]
            if not ys:
                continue
            ax.scatter([xpos[it]] * len(ys), ys, s=9, c=st.color,
                       alpha=0.45, linewidths=0, zorder=2)

    line_agg = "med" if spec.mode == "site" else spec.mode
    for st in styles:
        if not st.visible or st.gid not in data:
            continue
        reps = group_representatives(data[st.gid], excluded_keys,
                                     plotted, line_agg)
        pts = sorted((xpos[it], reps[it]) for it in plotted
                     if reps.get(it) is not None)
        if not pts:
            continue
        xs, ys = zip(*pts)
        if st.ref:
            ax.plot(xs, ys, color=REF_COLOR, marker="d", markersize=4,
                    linewidth=1.2, zorder=4, label=st.name)
        else:
            ax.plot(xs, ys, color=st.color, marker=MARKER.get(st.symbol, "o"),
                    markersize=3.5, linewidth=1.2, zorder=4, label=st.name)

    # X축(WIDTH·LENGTH) 위치에 세로 점선은 그리지 않는다 — 규격은 y값의 한계라
    # x 위치에 그으면 의미 없는 격자만 늘어난다(사용자 요청).
    for it in plotted:
        rule = rf.by_alias.get(it)
        if rule is None:
            continue
        if rule.target is not None:
            ax.plot([xpos[it]], [rule.target], marker="x",
                    color=TARGET_COLOR, markersize=11,
                    markeredgewidth=2.0, zorder=6, linestyle="none")

    if lgy:
        ax.set_yscale("log")
    ax.set_xlim(xlo, xhi)
    ax.set_ylim(ylo, yhi)
    ax.set_box_aspect(1)
    _flush_spec_box(ax)

    def _axis_name(name: str, aliases: list[str]) -> str:
        if name:
            return name
        u = next((rf.by_alias[a].unit for a in aliases
                  if a in rf.by_alias and rf.by_alias[a].unit), "")
        return ", ".join(aliases) + (f" [{u}]" if u else "")

    fs = _font_size(figsize, compact)
    ax.set_xlabel(geom, fontsize=fs)
    ax.set_ylabel(_axis_name(spec.y_name, plotted), fontsize=fs)
    if not compact:
        ax.set_title(spec.title, fontsize=fs + 1, fontweight="bold", loc="left")
    ax.tick_params(labelsize=max(FONT_MIN_PT, fs - 0.8),
                   pad=1 if compact else 3, length=2 if compact else 3)
    if compact:
        ax.xaxis.set_major_locator(MaxNLocator(3))
        ax.yaxis.set_major_locator(MaxNLocator(3))
    ax.grid(True, color="#ececee", lw=0.6, zorder=0)
    for sp in ax.spines.values():
        sp.set_color("#d2d2d7")
    if not compact and ax.get_legend_handles_labels()[0]:
        leg = ax.legend(fontsize=fs - 0.5, frameon=True, framealpha=0.95,
                        loc="upper left", bbox_to_anchor=(1.015, 1.0),
                        borderaxespad=0, markerscale=0.85)
        leg.get_frame().set_edgecolor("#d2d2d7")
        leg.get_frame().set_linewidth(0.6)
    fig.tight_layout(pad=0.8 if compact else 0.9)
    return fig


def _spec_box(ax, x_lo, x_hi, y_lo, y_hi) -> None:
    """축 범위가 확정된 뒤 그리도록 정보만 얹어 둔다."""
    ax._spec_bounds = (x_lo, x_hi, y_lo, y_hi)


def _flush_spec_box(ax) -> None:
    b = getattr(ax, "_spec_bounds", None)
    if not b or all(v is None for v in b):
        return
    x_lo, x_hi, y_lo, y_hi = b
    ax0, ax1 = ax.get_xlim()
    ay0, ay1 = ax.get_ylim()
    left = x_lo if x_lo is not None else ax0
    right = x_hi if x_hi is not None else ax1
    bottom = y_lo if y_lo is not None else ay0
    top = y_hi if y_hi is not None else ay1
    ax.add_patch(Rectangle(
        (left, bottom), right - left, top - bottom,
        fill=False, edgecolor=SPEC_COLOR, linewidth=1.2,
        linestyle="-", zorder=2.2, clip_on=True))
