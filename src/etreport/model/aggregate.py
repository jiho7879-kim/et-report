"""집계 헬퍼 — 화면·xlsx·PPT가 같은 숫자를 쓰도록 한 곳에 모은다.

성능 주의: 예전에는 (item × lot × wafer)마다 DataFrame을 필터링해서
item 20개 × wafer 25장이면 500번을 훑었다. 여기서는 group_by 한 번으로
전부 계산하고 조회는 dict 인덱싱으로 끝낸다.
"""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass
class WaferStats:
    """(lot, wafer) → {alias: 값} 그리고 제외된 개수."""
    values: dict[tuple[str, str], dict[str, float | None]]
    excluded: dict[tuple[str, str], int]

    def get(self, alias: str, lot: str, wafer: str) -> float | None:
        return self.values.get((lot, wafer), {}).get(alias)

    def ex(self, lot: str, wafer: str) -> int:
        return self.excluded.get((lot, wafer), 0)


def _agg_expr(agg: str, alias: str) -> pl.Expr:
    """agg 종류별 집계 식 — avg·med, 그 외(기존 "std")는 표본표준편차(n-1)."""
    if agg == "avg":
        return pl.col(alias).mean()
    if agg == "med":
        return pl.col(alias).median()
    return pl.col(alias).std(ddof=1)


def wafer_stats(data: pl.DataFrame, excluded: set[str],
                aliases: list[str], agg: str = "avg") -> WaferStats:
    """wafer별 평균·중앙값 또는 표본표준편차(n-1)를 한 번에 계산."""
    present = [a for a in aliases if a in data.columns]
    active = (data.filter(~pl.col("key").is_in(list(excluded)))
              if excluded else data)

    values: dict[tuple[str, str], dict[str, float | None]] = {}
    if present and not active.is_empty():
        expr = [_agg_expr(agg, a).alias(a) for a in present]
        grouped = active.group_by(["lot", "wafer"]).agg(expr)
        for rec in grouped.iter_rows(named=True):
            values[(rec["lot"], rec["wafer"])] = {
                a: (None if rec[a] is None else float(rec[a])) for a in present}

    # 제외 개수 — 전체 건수와 남은 건수의 차
    excluded_n: dict[tuple[str, str], int] = {}
    if excluded and not data.is_empty():
        total = {(r["lot"], r["wafer"]): r["len"] for r in
                 data.group_by(["lot", "wafer"]).len().iter_rows(named=True)}
        left = {(r["lot"], r["wafer"]): r["len"] for r in
                active.group_by(["lot", "wafer"]).len().iter_rows(named=True)}
        excluded_n = {k: v - left.get(k, 0) for k, v in total.items()
                      if v - left.get(k, 0) > 0}
    return WaferStats(values, excluded_n)


def group_representatives(data: pl.DataFrame, excluded: set[str],
                          aliases: list[str],
                          agg: str = "med") -> dict[str, float | None]:
    """그룹 전체(제외 반영)의 item별 대표값 — (lot,wafer) 집계의 평균 (trend 라인 연결용)."""
    st = wafer_stats(data, excluded, aliases, agg)
    reps: dict[str, float | None] = {}
    for a in aliases:
        vals = [v for per_wafer in st.values.values()
                if (v := per_wafer.get(a)) is not None]
        reps[a] = (sum(vals) / len(vals)) if vals else None
    return reps


def ref_values(data: pl.DataFrame, excluded: set[str], ref_gid: str | None,
               aliases: list[str], agg: str = "avg") -> dict[str, float | None]:
    """REF 그룹 전체의 대표값 — Δ 계산용."""
    if not ref_gid:
        return {}
    present = [a for a in aliases if a in data.columns]
    sub = (data.filter(~pl.col("key").is_in(list(excluded)))
           if excluded else data).filter(pl.col("gid") == ref_gid)
    if sub.is_empty() or not present:
        return {}
    expr = [_agg_expr(agg, a).alias(a) for a in present]
    rec = sub.select(expr).row(0, named=True)
    return {a: (None if rec[a] is None else float(rec[a])) for a in present}


def offspec(value: float | None, rule) -> bool:
    if value is None or rule is None:
        return False
    return bool((rule.speclow is not None and value < rule.speclow)
                or (rule.spechigh is not None and value > rule.spechigh))
