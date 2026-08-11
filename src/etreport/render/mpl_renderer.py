"""matplotlib 렌더러 — PlotSpec 하나 → Figure 하나.

화면(pyqtgraph)과 심볼·색·축·규격선이 일치해야 한다: 스펙과 축 규칙을
공유하고(ranges.py), 심볼 매핑은 아래 표 하나로 관리한다. 골든 이미지
테스트로 회귀를 잡는다(tests/ 참조).
배경은 흰색 — PPT에 그대로 들어간다.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import polars as pl
from matplotlib.patches import Rectangle

from etreport import fonts
from etreport.data.reformatter import Reformatter
from etreport.model.specs import GroupStyle, PlotSpec
from etreport.render.ranges import resolve_axes

# pyqtgraph 심볼 ↔ matplotlib 마커 (한 곳에서만 정의)
MARKER = {"o": "o", "s": "s", "t": "^", "d": "D", "+": "+"}
SPEC_COLOR = "#d70015"      # 규격 박스 — 빨간 실선
TARGET_COLOR = "#0071e3"    # 타깃 — 파란 X
REF_FILL = (0.56, 0.56, 0.58, 0.10)
FONT_MIN_PT = 6            # 6pt 하한 (계획서 §9)

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
           fig: plt.Figure | None = None) -> plt.Figure:
    """compact=True면 슬롯/미니용 — 라벨을 줄이고 여백을 좁힌다.

    fig를 주면 그 Figure에 그린다(화면 캔버스용). 안 주면 새로 만든다(PPT용).
    """
    if fig is None:
        fig, ax = plt.subplots(figsize=figsize, dpi=110 if compact else 150)
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
    for st in styles:
        if not st.visible or st.gid not in data:
            continue
        df = data[st.gid]
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

    # REF μ±3σ 밴드 (첫 y item 기준)
    if spec.ref_band:
        ref = next((s for s in styles if s.ref and s.gid in data), None)
        if ref is not None:
            y0 = pairs[0][1]
            ys = data[ref.gid][y0].drop_nulls()
            if ys.len() > 1:
                mu, sd = float(ys.mean()), float(ys.std(ddof=1))
                ax.axhspan(mu - 3 * sd, mu + 3 * sd, color=REF_FILL, zorder=1)
                ax.axhline(mu, color="#8e8e93", lw=0.9, ls="--", zorder=2)

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

    fs = max(FONT_MIN_PT, min(9.5, figsize[0] * 2.2))
    if compact:
        fs = max(FONT_MIN_PT, min(7.5, figsize[0] * 2.6))
    ax.set_xlabel(_axis_name(spec.x_name, [p[0] for p in pairs]), fontsize=fs)
    ax.set_ylabel(_axis_name(spec.y_name, [p[1] for p in pairs]), fontsize=fs)
    if not compact:
        ax.set_title(spec.title, fontsize=fs + 1, fontweight="bold", loc="left")
    ax.tick_params(labelsize=max(FONT_MIN_PT, fs - 1.5),
                   pad=1 if compact else 3, length=2 if compact else 3)
    if compact:
        ax.xaxis.set_major_locator(plt.MaxNLocator(4))
        ax.yaxis.set_major_locator(plt.MaxNLocator(4))
    ax.grid(True, color="#ececee", lw=0.6, zorder=0)
    for sp in ax.spines.values():
        sp.set_color("#d2d2d7")
    if not compact and ax.get_legend_handles_labels()[0]:
        leg = ax.legend(fontsize=fs - 0.5, frameon=True, framealpha=0.95,
                        loc="upper left", bbox_to_anchor=(1.015, 1.0),
                        borderaxespad=0, markerscale=0.85)
        leg.get_frame().set_edgecolor("#d2d2d7")
        leg.get_frame().set_linewidth(0.6)
    fig.tight_layout(pad=0.4 if compact else 0.8)
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
