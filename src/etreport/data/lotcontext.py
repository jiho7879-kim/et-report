"""분석 중인 lot이 **어떤 조건으로 흘렀는지**를 ET DB에서 읽어 온다.

fab tracking(`fab.f_fab_tracking`)과 inline 계측(`fab.f_fab_wf_met`)을 조회할 때
사용자가 매번 손으로 적던 값들 — line_id · process_id · part_id · 기간 — 을
**분석 중인 DuckDB에서 그대로 꺼내** 조회 창의 기본값으로 채운다. 손으로 적으면
오타 하나에 조회가 비고, 그때는 "데이터가 없는 것"과 구별되지 않는다.

기간 규칙(확정): **ET tkout_time 기준 180일 이전부터 tkout_time까지**.
계측·tracking은 ET보다 앞선 공정에서 찍히므로 ET 시각 이후를 보는 것은 의미가
없고, 180일이면 통상적인 lot 흐름(수 주~수 개월)을 넉넉히 덮는다. 화면에서
언제든 고칠 수 있다 — 여기서 정하는 것은 **기본값**일 뿐이다.

컬럼이 없는 스키마에서도 조용히 빈 값을 돌려준다(§오류 처리: 중단하지 않는다).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from etreport.data import compat

log = logging.getLogger(__name__)

#: ET tkout_time에서 며칠을 거슬러 올라가 조회할지(기본값).
DEFAULT_LOOKBACK_DAYS = 180

#: DB에서 뽑아 오는 조건 역할 → 조회 창에서 부를 이름.
CONTEXT_ROLES = ("line", "process", "part")

#: 한 역할에서 몇 개까지 모아 올지. lot이 여럿이면 값도 여럿일 수 있는데,
#: 수십 개가 조회 조건에 그대로 들어가면 SQL이 읽을 수 없게 길어진다.
MAX_VALUES = 20


@dataclass
class LotContext:
    """분석 중인 lot들의 조회 조건 기본값."""
    lots: list[str] = field(default_factory=list)
    line_ids: list[str] = field(default_factory=list)
    process_ids: list[str] = field(default_factory=list)
    part_ids: list[str] = field(default_factory=list)
    tkout_from: date | None = None      # 가장 이른 ET 측정일
    tkout_to: date | None = None        # 가장 늦은 ET 측정일

    def line_id(self, default: str = "") -> str:
        """조회에 쓸 line — 여러 개면 첫 번째(조회는 하나만 받는다)."""
        return self.line_ids[0] if self.line_ids else default

    def date_range(self, lookback: int = DEFAULT_LOOKBACK_DAYS
                   ) -> tuple[date | None, date | None]:
        """(시작, 끝) — ET 측정일 기준 `lookback`일 이전부터 측정일까지.

        ET 시각을 모르면 (None, None) — 부르는 쪽이 기간 없이 조회하거나
        사용자에게 직접 받는다. 없는 기간을 오늘 기준으로 지어내지 않는다:
        6개월 전 lot을 분석하는 중이면 오늘 기준 180일은 통째로 빗나간다.
        """
        if self.tkout_to is None:
            return None, None
        start = (self.tkout_from or self.tkout_to) - timedelta(days=lookback)
        return start, self.tkout_to

    def summary(self) -> str:
        """조회 창 위에 한 줄로 — 무엇을 근거로 채웠는지 보이게."""
        lo, hi = self.date_range()
        parts = [f"lot {len(self.lots)}개"]
        for label, vals in (("line", self.line_ids), ("process", self.process_ids),
                            ("part", self.part_ids)):
            if vals:
                parts.append(f"{label} {', '.join(vals[:3])}"
                             + (f" 외 {len(vals) - 3}" if len(vals) > 3 else ""))
        if lo and hi:
            parts.append(f"{lo:%Y-%m-%d} ~ {hi:%Y-%m-%d}")
        return " · ".join(parts)


def empty() -> LotContext:
    return LotContext()


def from_db(db_path: str, lots: list[str] | None = None) -> LotContext:
    """ET DuckDB에서 조회 조건을 읽어 온다. 실패하면 빈 컨텍스트.

    **[적용] 없이도 돌아야 한다** — 사용자는 DB만 고르고 fab tracking부터 볼 수
    있다. item 컬럼을 전혀 건드리지 않으므로 큰 DB에서도 한 번 훑고 끝난다.
    읽기 전용 연결은 반드시 `loader.readonly_query()`를 거친다(설정이 다른 연결을
    같은 파일에 열면 DuckDB가 막고, 닫지 않은 연결은 조회 캐시를 붙잡는다).
    """
    from pathlib import Path

    from etreport.data.loader import readonly_query

    if not db_path or not Path(db_path).exists():
        return empty()
    try:
        with readonly_query(db_path) as con:
            tbl = compat.pick_table(con)
            if tbl is None:
                return empty()
            prof = compat.profile(con, tbl)
            return _read(con, prof, lots)
    except Exception as e:                    # noqa: BLE001 — 기본값일 뿐이다
        log.warning("lot 조건 조회 실패(%s) — 빈 값으로 진행", e)
        return empty()


def _read(con, prof, lots: list[str] | None) -> LotContext:
    ctx = LotContext(lots=list(lots or []))
    # `lot_filter`는 이미 ' WHERE …' 또는 빈 문자열이다 — 여기서 조건을 더할 때
    # WHERE/AND를 잘못 이어 붙이지 않도록 접속사를 한 번만 정한다.
    where = compat.lot_filter(prof, lots)
    join = " AND" if where else " WHERE"
    for role, attr in (("line", "line_ids"), ("process", "process_ids"),
                       ("part", "part_ids")):
        col = prof.roles.get(role)
        if not col:
            continue
        rows = con.execute(
            f'SELECT DISTINCT "{col}" FROM "{prof.table}"{where}'
            f'{join} "{col}" IS NOT NULL ORDER BY 1 LIMIT {MAX_VALUES}').fetchall()
        setattr(ctx, attr, [str(r[0]) for r in rows])

    tcol = prof.roles.get("time")
    if tcol:
        # 문자열로 적힌 시각도 있다(예전 DB) — TRY_CAST로 못 읽는 값만 버린다.
        lo, hi = con.execute(
            f'SELECT min(TRY_CAST("{tcol}" AS TIMESTAMP)), '
            f'max(TRY_CAST("{tcol}" AS TIMESTAMP)) '
            f'FROM "{prof.table}"{where}').fetchone()
        ctx.tkout_from = lo.date() if lo is not None else None
        ctx.tkout_to = hi.date() if hi is not None else None
    if not ctx.lots and (lcol := prof.roles.get("lot")):
        ctx.lots = [str(r[0]) for r in con.execute(
            f'SELECT DISTINCT "{lcol}" FROM "{prof.table}" ORDER BY 1').fetchall()]
    return ctx
