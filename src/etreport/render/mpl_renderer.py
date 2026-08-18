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
TARGET_COLOR = "#0000FF"    # 타깃 — 파란 X
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


def lot_markers(data: dict[str, pl.DataFrame]) -> dict[str, str]:
    """lot → matplotlib marker. **data 전체**를 한 번에 보고 정한다(§9.2).

    그룹마다 따로 매기면 같은 lot이 그룹에 따라 다른 모양이 되고, plot마다 따로
    매기면 같은 lot이 페이지마다 달라진다. 정렬한 lot 순서로 한 번만 정해
    화면·PPT·모든 페이지에서 같은 lot이 늘 같은 모양이 되게 한다.

    lot이 marker 종류보다 많으면 순환한다 — 실무는 한 번에 2~3 lot이다.
    """
    lots = sorted({str(v) for df in data.values() if "lot" in df.columns
                   for v in df["lot"].unique().to_list() if v is not None})
    marks = list(MARKER.values())
    return {lot: marks[i % len(marks)] for i, lot in enumerate(lots)}


def _lot_parts(df: pl.DataFrame, lot_split: bool, markers: dict[str, str],
               st: GroupStyle) -> list[tuple[pl.DataFrame, str, str]]:
    """(하위 프레임, marker, 범례 라벨) 목록.

    lot 구분이 꺼져 있으면 한 덩이 그대로 — 지금까지의 그림과 완전히 같다.
    켜져 있으면 lot마다 나누고 **그룹의 symbol 대신 lot이 모양을 정한다**.
    프레임을 만드는 쪽(화면·PPT)이 아니라 여기서 나누는 이유는, 만드는 곳이
    두 군데라 거기서 쪼개면 "화면 = PPT"가 깨지기 때문이다.
    """
    base = MARKER.get(st.symbol, "o")
    if not lot_split or "lot" not in df.columns or df.is_empty():
        return [(df, base, st.name)]
    parts = []
    for lot in sorted({str(v) for v in df["lot"].unique().to_list()
                       if v is not None}):
        sub = df.filter(pl.col("lot").cast(pl.Utf8) == lot)
        if not sub.is_empty():
            parts.append((sub, markers.get(lot, base), f"{st.name} ({lot})"))
    return parts or [(df, base, st.name)]


def render(spec: PlotSpec,
           data: dict[str, pl.DataFrame],      # gid → (x, y, alias별 값 wide)
           styles: list[GroupStyle],
           rf: Reformatter,
           log_patterns: list[str],
           figsize: tuple[float, float],
           excluded: pl.DataFrame | None = None,
           compact: bool = False,
           fig: Figure | None = None,
           lot_split: bool = False) -> Figure:
    """compact=True면 슬롯/미니용 — 라벨을 줄이고 여백을 좁힌다.

    fig를 주면 그 Figure에 그린다(화면 캔버스용). 안 주면 새로 만든다(PPT용).

    새로 만들 때 pyplot을 쓰지 않는다 — pyplot은 만든 Figure를
    전역 매니저에 등록해 두기 때문에, 명시적으로 닫지 않으면 덱 하나를 만들 때
    생긴 수백 개의 Figure가 프로세스가 끝날 때까지 메모리에 남는다.

    `lot_split=True`면 lot마다 심볼을 달리하고 범례에 lot을 병기한다 — 여러 lot을
    한 그림에 놓고 볼 때 어느 점이 어느 lot인지 구별하기 위해서다.
    """
    if spec.type == "trend":
        return _render_trend(spec, data, styles, rf, log_patterns, figsize,
                             excluded=excluded, compact=compact, fig=fig,
                             lot_split=lot_split)
    if spec.type == "box":
        return _render_box(spec, data, styles, rf, log_patterns, figsize,
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
    markers = lot_markers(data) if lot_split else {}
    for st in styles:
        if not st.visible or st.gid not in data:
            continue
        df = data[st.gid]
        for sub, mark, label in _lot_parts(df, lot_split, markers, st):
            if spec.mode != "site":
                _scatter_aggregate(ax, sub, pairs, st, spec.mode, excluded_keys,
                                   marker=mark, label=label)
                continue
            for ax_x, ax_y in pairs:
                if ax_x not in sub.columns or ax_y not in sub.columns:
                    continue
                ax.scatter(sub[ax_x], sub[ax_y],
                           s=st.size ** 2, c=st.color, marker=mark,
                           linewidths=0, alpha=0.9, zorder=3,
                           label=label if (ax_x, ax_y) == pairs[0] else None)

    # 제외된 포인트 — 회색 빈 심볼로 남긴다(사라지지 않게)
    if excluded is not None and not excluded.is_empty():
        for ax_x, ax_y in pairs:
            if ax_x in excluded.columns and ax_y in excluded.columns:
                ax.scatter(excluded[ax_x], excluded[ax_y], s=26,
                           facecolors="none", edgecolors="#c7c7cc",
                           linewidths=0.9, zorder=2.5)

    # 규격 — 십자로 삐져나온 선 대신 **규격 창(박스)**을 빨간 실선으로.
    # 한쪽 규격이 없으면 그 변은 축 끝까지 열어 둔다(합집합, 확정 사양).
    # xy쌍이 여럿이면 **쌍마다 한 개씩** 그린다 — 쌍끼리 규격이 다른데 하나로
    # 합치면 어느 쪽에도 맞지 않는 창이 나온다. 색은 쌍을 구분하지 않고 그대로
    # 규격 빨강 하나다(사용자 요청).
    def _bounds(alias: str) -> tuple[float | None, float | None]:
        rule = rf.by_alias.get(alias)
        if rule is None:
            return (None, None)
        return (rule.speclow, rule.spechigh)

    seen_box: set[tuple] = set()
    for ax_x, ax_y in pairs:
        x_lo, x_hi = _bounds(ax_x)
        y_lo, y_hi = _bounds(ax_y)
        box = (x_lo, x_hi, y_lo, y_hi)
        if box in seen_box:
            continue
        seen_box.add(box)
        _spec_box(ax, x_lo, x_hi, y_lo, y_hi)

    # 타깃 — 파란 X. 양축 모두 있으면 교점 하나, 한쪽만 있으면 그 축의 선.
    # 규격과 마찬가지로 쌍마다 하나씩(같은 위치면 한 번만).
    def _target(alias: str) -> float | None:
        rule = rf.by_alias.get(alias)
        return rule.target if rule is not None else None

    seen_tgt: set[tuple] = set()
    for ax_x, ax_y in pairs:
        tx, ty = _target(ax_x), _target(ax_y)
        if (tx, ty) in seen_tgt or (tx is None and ty is None):
            continue
        seen_tgt.add((tx, ty))
        if tx is not None and ty is not None:
            ax.plot([tx], [ty], marker="x", color=TARGET_COLOR, markersize=11,
                    markeredgewidth=2.0, zorder=6, linestyle="none",
                    label="_target")
        elif tx is not None:
            ax.axvline(tx, color=TARGET_COLOR, lw=1.1, zorder=2.2)
        else:
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
                       agg: str, excluded_keys: set[str],
                       marker: str | None = None,
                       label: str | None = None) -> None:
    """mode=avg/med/std scatter — (lot,wafer) 집계 점 하나씩.

    marker·label을 주면 그것을 쓴다(lot 구분). 안 주면 그룹 스타일 그대로다.
    """
    aliases = [a for pr in pairs for a in pr]
    mark = marker or MARKER.get(st.symbol, "o")
    name = st.name if label is None else label
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
        ax.scatter(xs, ys, s=st.size ** 2, c=st.color, marker=mark,
                   linewidths=0, alpha=0.9, zorder=3,
                   label=name if (ax_x, ax_y) == pairs[0] else None)


def _render_trend(spec: PlotSpec,
                  data: dict[str, pl.DataFrame],
                  styles: list[GroupStyle],
                  rf: Reformatter,
                  log_patterns: list[str],
                  figsize: tuple[float, float],
                  excluded: pl.DataFrame | None = None,
                  compact: bool = False,
                  fig: Figure | None = None,
                  lot_split: bool = False) -> Figure:
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

    markers = lot_markers(data) if lot_split else {}
    # 점 스트립은 lot으로 나누지 않는다 — 작은 점이라 모양을 구분하지 않고,
    # wafer 집계는 어차피 (lot, wafer)별이라 나눠도 같은 점이 나온다.
    for st in styles:
        if not st.visible or st.gid not in data:
            continue
        df = data[st.gid]
        # wafer 집계는 **item마다가 아니라 그룹마다 한 번**이다 — wafer_stats는
        # alias 여러 개를 group_by 한 번으로 함께 계산한다. item마다 부르면
        # 그 group_by가 item 수만큼 반복된다(alias 25개 기준 실측 19배).
        ws = (None if spec.mode == "site"
              else wafer_stats(df, excluded_keys, plotted, spec.mode))
        for it in plotted:
            if ws is None:
                if it not in df.columns:
                    continue
                ys = df[it].drop_nulls().to_list()
            else:
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
        # lot 구분이 켜져 있으면 lot마다 선을 따로 긋는다 — 한 선으로 합치면
        # lot 사이의 차이가 대표값 하나에 섞여 보이지 않는다.
        for sub, mark, label in _lot_parts(data[st.gid], lot_split,
                                           markers, st):
            reps = group_representatives(sub, excluded_keys, plotted, line_agg)
            pts = sorted((xpos[it], reps[it]) for it in plotted
                         if reps.get(it) is not None)
            if not pts:
                continue
            xs, ys = zip(*pts)
            if st.ref and not lot_split:
                ax.plot(xs, ys, color=REF_COLOR, marker="d", markersize=4,
                        linewidth=1.2, zorder=4, label=label)
            else:
                ax.plot(xs, ys, color=REF_COLOR if st.ref else st.color,
                        marker=mark, markersize=3.5, linewidth=1.2,
                        zorder=4, label=label)

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


#: boxplot 수염 길이 — 표준 Tukey 1.5×IQR. 이상치 **필터**(config의 배수)와는
#: 다른 값이다: 그림은 늘 같은 눈으로 읽혀야 하고, 필터는 무엇을 버릴지의 문제다.
BOX_WHIS = 1.5
BOX_MAX_LABELS = 24        # 이보다 많으면 축 이름을 기울여 적는다


def _render_box(spec: PlotSpec,
                data: dict[str, pl.DataFrame],
                styles: list[GroupStyle],
                rf: Reformatter,
                log_patterns: list[str],
                figsize: tuple[float, float],
                excluded: pl.DataFrame | None = None,
                compact: bool = False,
                fig: Figure | None = None) -> Figure:
    """boxplot — x는 **범주**, y는 item 값.

    산점도와 다른 점은 x가 데이터 컬럼(숫자)이 아니라 나눌 기준이라는 것뿐이다.
    무엇으로 나눌 수 있는지와 값을 만드는 법은 `model/categories.py`가 갖는다 —
    화면·PPT가 같은 함수를 쓰므로 "화면 = PPT"가 그대로 유지된다.

    그룹이 여럿이면 한 범주 자리에 상자를 **나란히** 놓고 그룹 색을 칠한다.
    겹쳐 그리면 어느 상자가 어느 그룹인지 알 수 없다.

    y축 범위·로그 판정은 산점도와 같은 규칙(`render/ranges.py`)이다. x축은
    범주라 규격이 의미가 없고, 대신 y의 규격선·타깃을 가로선으로 긋는다.
    """
    from etreport.model import categories as cat

    if fig is None:
        fig = Figure(figsize=figsize, dpi=140 if compact else 180)
    else:
        fig.clear()
    ax = fig.add_subplot(111)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    xname = spec.x.strip()
    items = [t.strip() for t in spec.y.split(",") if t.strip()]
    excluded_keys = (set(excluded["key"]) if excluded is not None
                     and not excluded.is_empty() else set())
    labels = cat.group_labels(styles)

    # ── 상자 하나하나의 값 모으기 ────────────────────────────
    # (그룹, 범주) → 값들. 범주 순서는 전체를 모아 한 번에 정한다 — 그룹마다
    # 따로 정하면 같은 범주가 그룹에 따라 다른 자리에 놓인다.
    series: dict[str, dict[str, list[float]]] = {}
    seen: list[str] = []
    for st in styles:
        if not st.visible or st.gid not in data:
            continue
        df = data[st.gid]
        if df.is_empty():
            continue
        vals = _box_values(df, xname, items, spec.mode, excluded_keys, labels)
        series[st.gid] = vals
        seen += list(vals)
    cats = cat.order(seen)
    if not cats:
        ax.text(0.5, 0.5, "그릴 값이 없습니다", ha="center", va="center",
                transform=ax.transAxes, fontsize=_font_size(figsize, compact),
                color="#8e8e93")
        ax.set_xticks([])
        ax.set_yticks([])
        fig.tight_layout(pad=0.8 if compact else 0.9)
        return fig

    drawn = [st for st in styles if st.gid in series]
    width = 0.8 / max(1, len(drawn))
    # 자리는 **범주마다 그 자리에 실제로 값이 있는 그룹끼리만** 나눈다. 전체 그룹
    # 수로 나누면, 그룹과 범주가 1:1인 경우(x축을 그룹으로 놓았을 때)에 상자
    # 하나가 눈금에서 비켜서 그려진다.
    here = {c: [st.gid for st in drawn if series[st.gid].get(c)] for c in cats}
    for st in drawn:
        pos, vals = [], []
        for j, c in enumerate(cats):
            v = series[st.gid].get(c) or []
            if not v:
                continue
            mates = here[c]
            k, m = mates.index(st.gid), len(mates)
            pos.append(j + (k - (m - 1) / 2) * width)
            vals.append(v)
        if not vals:
            continue
        color = REF_COLOR if st.ref else st.color
        bp = ax.boxplot(vals, positions=pos, widths=width * 0.82,
                        whis=BOX_WHIS, patch_artist=True, manage_ticks=False,
                        flierprops={"marker": ".", "markersize": 3,
                                    "markerfacecolor": color,
                                    "markeredgecolor": "none", "alpha": 0.6},
                        medianprops={"color": "#12161B", "linewidth": 1.1},
                        whiskerprops={"color": color, "linewidth": 0.9},
                        capprops={"color": color, "linewidth": 0.9})
        for patch in bp["boxes"]:
            patch.set_facecolor(color)
            patch.set_alpha(0.35)
            patch.set_edgecolor(color)
            patch.set_linewidth(1.0)
        # 범례는 상자가 아니라 대리 선으로 — boxplot은 라벨을 받지 않는다
        ax.plot([], [], color=color, linewidth=6, alpha=0.55, label=st.name)

    # ── 축 ───────────────────────────────────────────────────
    lgy = resolve_log(spec.logy_mode, items, log_patterns)
    ys = [v for by in series.values() for vs in by.values() for v in vs]
    if spec.range_mode == "manual" and None not in (spec.ymin, spec.ymax):
        ylo, yhi = spec.ymin, spec.ymax
    else:
        ylo, yhi = compute_range(items, min(ys) if ys else None,
                                 max(ys) if ys else None, rf, lgy)
    if lgy:
        ax.set_yscale("log")
    ax.set_ylim(ylo, yhi)
    ax.set_xlim(-0.6, len(cats) - 0.4)
    ax.set_xticks(range(len(cats)))
    rot = 45 if (len(cats) > 6 or max(len(c) for c in cats) > 6) else 0
    ax.set_xticklabels(cats, rotation=rot,
                       ha="right" if rot else "center")
    # 다 적으면 서로 겹쳐 **아무것도** 안 읽힌다 — 몇 개 걸러 적는다.
    # 상자는 전부 그대로 그린다(값을 감추는 게 아니라 이름만 솎는다).
    limit = BOX_MAX_LABELS // 2 if compact else BOX_MAX_LABELS
    if len(cats) > limit:
        stride = -(-len(cats) // limit)          # 올림 나눗셈
        for k, lab in enumerate(ax.get_xticklabels()):
            lab.set_visible(k % stride == 0)

    # 규격·타깃은 y축 가로선으로. x가 범주라 규격 '창'은 그릴 수 없다.
    for item in items:
        rule = rf.by_alias.get(item)
        if rule is None:
            continue
        for bound in (rule.speclow, rule.spechigh):
            if bound is not None:
                ax.axhline(bound, color=SPEC_COLOR, lw=1.2, zorder=2.2)
        if rule.target is not None:
            ax.axhline(rule.target, color=TARGET_COLOR, lw=1.1, zorder=2.2)

    fs = _font_size(figsize, compact)
    ax.set_xlabel(spec.x_name or xname, fontsize=fs)
    u = next((rf.by_alias[a].unit for a in items
              if a in rf.by_alias and rf.by_alias[a].unit), "")
    ax.set_ylabel(spec.y_name or ", ".join(items) + (f" [{u}]" if u else ""),
                  fontsize=fs)
    if not compact:
        ax.set_title(spec.title, fontsize=fs + 1, fontweight="bold", loc="left")
    ax.tick_params(labelsize=max(FONT_MIN_PT, fs - 0.8),
                   pad=1 if compact else 3, length=2 if compact else 3)
    ax.grid(True, axis="y", color="#ececee", lw=0.6, zorder=0)
    for sp in ax.spines.values():
        sp.set_color("#d2d2d7")
    if not compact and ax.get_legend_handles_labels()[0]:
        leg = ax.legend(fontsize=fs - 0.5, frameon=True, framealpha=0.95,
                        loc="upper left", bbox_to_anchor=(1.015, 1.0),
                        borderaxespad=0)
        leg.get_frame().set_edgecolor("#d2d2d7")
        leg.get_frame().set_linewidth(0.6)
    fig.tight_layout(pad=0.8 if compact else 0.9)
    return fig


def _box_values(df: pl.DataFrame, xname: str, items: list[str], mode: str,
                excluded_keys: set[str], labels: dict[str, str]
                ) -> dict[str, list[float]]:
    """한 그룹의 프레임 → {범주: 값들}.

    `mode`가 site면 측정점 값을 그대로, 그 밖이면 (lot, wafer) 집계값 하나씩
    쓴다 — 산점도의 점 표시 방식과 같은 뜻이어야 한다. 집계일 때 범주는 그
    wafer의 **첫 값**으로 정한다(한 wafer가 두 범주에 걸치는 일은 없다:
    tracking 컬럼도 lot·wafer 단위로 붙는다).
    """
    from etreport.model import categories as cat

    cols = [c for c in items if c in df.columns]
    if not cols:
        return {}
    if excluded_keys and "key" in df.columns:
        df = df.filter(~pl.col("key").is_in(list(excluded_keys)))
    if df.is_empty():
        return {}
    keys = cat.series(df, xname, labels)
    work = df.with_columns(keys.alias("_cat"))

    if mode == "site":
        out: dict[str, list[float]] = {}
        for c in cols:
            sub = work.select(["_cat", c]).drop_nulls()
            for k, v in zip(sub["_cat"].to_list(), sub[c].to_list()):
                out.setdefault(str(k), []).append(float(v))
        return out

    agg = {"avg": pl.mean, "med": pl.median}.get(mode)
    expr = ([pl.col(c).mean().alias(c) for c in cols] if agg is pl.mean else
            [pl.col(c).median().alias(c) for c in cols] if agg is pl.median else
            [pl.col(c).std(ddof=1).alias(c) for c in cols])
    per_wafer = (work.group_by(["lot", "wafer"], maintain_order=True)
                 .agg(pl.col("_cat").first().alias("_cat"), *expr))
    out = {}
    for c in cols:
        sub = per_wafer.select(["_cat", c]).drop_nulls()
        for k, v in zip(sub["_cat"].to_list(), sub[c].to_list()):
            out.setdefault(str(k), []).append(float(v))
    return out


def _spec_box(ax, x_lo, x_hi, y_lo, y_hi) -> None:
    """축 범위가 확정된 뒤 그리도록 정보만 얹어 둔다(xy쌍마다 하나씩 쌓인다)."""
    ax._spec_bounds = [*getattr(ax, "_spec_bounds", []),
                       (x_lo, x_hi, y_lo, y_hi)]


def _flush_spec_box(ax) -> None:
    for b in getattr(ax, "_spec_bounds", []):
        if all(v is None for v in b):
            continue
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
