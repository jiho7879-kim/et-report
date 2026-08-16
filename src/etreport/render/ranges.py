"""축 규칙 — 화면과 PPT가 반드시 같은 값을 쓰도록 여기 한 곳에만 둔다.

범위(확정): lo = min(SPECLOW, 데이터 min), hi = max(SPECHIGH, 데이터 max)
            → 중심 기준으로 폭 ×1.2 하여 항상 고정.
로그: item 이름 패턴 리스트(설정)에 걸리면 자동 log. plot별 오버라이드 가능.
멀티스캐터: 축에 쓰인 모든 item의 규격 합집합으로 범위, 규격선 전부 표시(확정).

**여백은 축이 보이는 공간에서 준다** — 로그 축이면 ×1.2도 로그 공간에서.
선형 폭의 10%는 decade가 여럿인 축에서 0.04 decade밖에 안 돼 여백이 사라지고,
아래쪽은 음수가 되어 잘리는 바람에 최솟값 점이 축선에 붙어 반쯤 그려졌다.
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


#: 축 폭 배율 — 중심 기준으로 양쪽 10%씩 여백(확정 사양).
MARGIN = 1.2

#: 값이 하나뿐일 때 줄 여백 — 값 크기에 비례한다. 예전처럼 ±0.5로 고정하면
#: 1e-9짜리 누설은 축이 -0.6~0.6이 되어 점이 0에 눌러붙고, 1e6짜리 값은
#: 여백이 폭 0이나 마찬가지가 된다.
FLAT_PAD = 0.05


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
    if log_scale:
        return _log_range(lo, hi, data_min)
    span, ref = hi - lo, max(abs(lo), abs(hi))
    if span <= ref * 1e-9:                      # 값이 하나뿐(또는 전부 같다)
        pad = ref * FLAT_PAD or 0.5             # 0 근처면 절대 여백으로
        lo, hi = lo - pad, hi + pad
    c, half = (lo + hi) / 2, (hi - lo) / 2 * MARGIN
    return c - half, c + half


def _log_range(lo: float, hi: float,
               data_min: float | None) -> tuple[float, float]:
    """로그 축 범위 — 여백을 decade(로그 공간)로 준다.

    0·음수는 로그 축에 그릴 수 없으므로 하한은 아는 양수 중 가장 작은 것으로
    잡는다(규격 하한이 0인 item이 흔하다). 데이터가 전부 0 이하면 상한 아래
    3 decade를 열어 둔다 — 어차피 matplotlib이 그 점들을 버린다.
    """
    if hi <= 0:
        hi = 1.0
    if lo <= 0:
        lo = data_min if (data_min or 0) > 0 else hi / 1000.0
    if lo > hi:
        lo, hi = hi, lo
    a, b = math.log10(lo), math.log10(hi)
    if b - a < 1e-9:                            # 값이 하나뿐 — 위아래 반 decade
        a, b = a - 0.5, b + 0.5
    c, half = (a + b) / 2, (b - a) / 2 * MARGIN
    return 10.0 ** (c - half), 10.0 ** (c + half)


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
