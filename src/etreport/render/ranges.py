"""축 규칙 — 화면과 PPT가 반드시 같은 값을 쓰도록 여기 한 곳에만 둔다.

범위(확정): lo = min(SPECLOW, 데이터 min), hi = max(SPECHIGH, 데이터 max)
            → 중심 기준으로 폭 ×1.2 하여 항상 고정.
로그: item 이름 패턴 리스트(설정)에 걸리면 자동 log. plot별 오버라이드 가능.
멀티스캐터: 축에 쓰인 모든 item의 규격 합집합으로 범위, 규격선 전부 표시(확정).
"""
from __future__ import annotations

import fnmatch
import math

from etreport.data.reformatter import Reformatter
from etreport.model.specs import PlotSpec


def is_log(alias: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(alias.lower(), p.lower()) for p in patterns)


def resolve_log(mode: str, aliases: list[str], patterns: list[str]) -> bool:
    if mode == "log":
        return True
    if mode == "linear":
        return False
    return any(is_log(a, patterns) for a in aliases)


def compute_range(aliases: list[str], data_min: float | None,
                  data_max: float | None, rf: Reformatter,
                  log_scale: bool = False) -> tuple[float, float]:
    lo = math.inf if data_min is None else data_min
    hi = -math.inf if data_max is None else data_max
    for a in aliases:
        r = rf.by_alias.get(a)
        if r is None:
            continue
        if r.speclow is not None:
            lo = min(lo, r.speclow)
        if r.spechigh is not None:
            hi = max(hi, r.spechigh)
    if not math.isfinite(lo) or not math.isfinite(hi):
        lo, hi = 0.0, 1.0
    if hi - lo < 1e-12:
        lo, hi = lo - 0.5, hi + 0.5
    c, half = (lo + hi) / 2, (hi - lo) / 2 * 1.2
    lo, hi = c - half, c + half
    if log_scale:
        lo = max(lo, min(abs(data_min or 1e-4), 1e-4))
    return lo, hi


def resolve_axes(spec: PlotSpec, rf: Reformatter, patterns: list[str],
                 data_ranges: dict[str, tuple[float, float]]):
    """spec + 데이터 min/max → ((xlo,xhi,logx),(ylo,yhi,logy)).

    data_ranges: alias → (min, max). pyqtgraph 쪽 주의 — log 모드의
    viewRange는 로그 공간이므로 호출 전 데이터 공간으로 정규화할 것(계획서 §9).
    """
    xs = [a for a, _ in spec.pairs()]
    ys = [b for _, b in spec.pairs()]
    lgx = resolve_log(spec.logx_mode, xs, patterns)
    lgy = resolve_log(spec.logy_mode, ys, patterns)

    def dminmax(al: list[str]):
        mins = [data_ranges[a][0] for a in al if a in data_ranges]
        maxs = [data_ranges[a][1] for a in al if a in data_ranges]
        return (min(mins) if mins else None, max(maxs) if maxs else None)

    if spec.range_mode == "manual" and None not in (
            spec.xmin, spec.xmax, spec.ymin, spec.ymax):
        return (spec.xmin, spec.xmax, lgx), (spec.ymin, spec.ymax, lgy)
    xm, xM = dminmax(xs)
    ym, yM = dminmax(ys)
    return (*compute_range(xs, xm, xM, rf, lgx), lgx), \
           (*compute_range(ys, ym, yM, rf, lgy), lgy)
