"""멀티 lot 커버리지 — lot마다 무엇이 빠졌는지 **한 곳에서** 판정한다(§9.2).

lot을 여러 개 놓고 보면 비교가 조용히 무의미해지는 경우가 있다. lot마다 setup된
item이 다르거나, 한 lot만 wafer가 절반이거나, 측정 조건(step·온도·site)이 어긋나
있으면 표는 멀쩡한 얼굴로 빈칸을 보여 준다. 빈칸이 "측정했는데 값이 없다"인지
"애초에 측정 항목이 아니다"인지는 표만 봐서 알 수 없다.

판정은 **기준 lot 대비**로 한다. 기준은 item이 가장 많은 lot이다 — 보통 가장 온전히
측정된 lot이고, 자동으로 정해지므로 사용자가 고를 것이 하나 줄어든다.

대상은 **지금 읽어 들인 lot만**이다. 도크에서 뺀 lot은 프레임에 아예 없고, 화면·표와
같은 범위를 보아야 해석이 엇갈리지 않는다.

이 모듈은 순수 함수다 — `state.data` 하나만 보고 계산하며 DB를 다시 조회하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

#: wafer당 포인트 수 중앙값이 기준 lot의 이 비율을 벗어나면 알린다.
#: 평균이 아니라 **중앙값**을 쓴다 — 측정이 덜 된 wafer 한 장이 평균을 끌어내려
#: 멀쩡한 lot을 경고로 만들기 때문이다.
POINT_TOLERANCE = 0.2

#: 문구에 늘어놓을 최대 개수. 넘으면 "외 N건"으로 접는다.
LIST_LIMIT = 5


def _cond_label(step: object, temp: object, site: object) -> str:
    """측정 조건 하나를 사람이 읽는 한 줄로. 없는 값은 `-`."""
    def one(v: object) -> str:
        return "-" if v is None or v == "" else str(v)
    return f"{one(step)}·{one(temp)}·{one(site)}"


def _fold(names: list[str], limit: int = LIST_LIMIT) -> str:
    head = ", ".join(names[:limit])
    return head + (f" 외 {len(names) - limit}건" if len(names) > limit else "")


@dataclass
class LotRow:
    """lot 하나의 커버리지."""
    lot: str
    wafers: int = 0
    items: int = 0                                   # 값이 하나라도 있는 item 수
    points_median: float = 0.0                       # wafer당 포인트 수 중앙값
    missing_items: list[str] = field(default_factory=list)   # 기준에 있고 여기 없는
    cond_missing: list[str] = field(default_factory=list)    # 기준에 있고 여기 없는
    cond_extra: list[str] = field(default_factory=list)      # 여기에만 있는
    point_ratio: float | None = None                 # 기준 lot 대비 (못 구하면 None)
    conditions: list[str] = field(default_factory=list)
    is_base: bool = False

    @property
    def point_off(self) -> bool:
        """포인트 수가 기준에서 너무 벗어났는가."""
        if self.point_ratio is None:
            return False
        return not (1 - POINT_TOLERANCE <= self.point_ratio <= 1 + POINT_TOLERANCE)


@dataclass
class CoverageReport:
    base_lot: str = ""
    rows: list[LotRow] = field(default_factory=list)

    def has_warnings(self) -> bool:
        return any(r.missing_items or r.cond_missing or r.cond_extra or r.point_off
                   for r in self.rows)

    def lines(self) -> list[str]:
        """[적용] 결과에 붙일 요약. 알릴 것이 없으면 빈 리스트.

        LoadReport.warnings에 그대로 들어가므로 `[커버리지]` 머리표를 붙여
        리포메터·템플릿 경고와 구분되게 한다.
        """
        if len(self.rows) < 2:            # lot이 하나면 비교할 대상이 없다
            return []
        out: list[str] = []
        base = self.base_lot
        base_row = next((r for r in self.rows if r.lot == base), None)
        for r in self.rows:
            if r.is_base:
                continue
            if r.missing_items:
                out.append(f"[커버리지] {r.lot}에 없는 item "
                           f"{len(r.missing_items)}개 (기준 {base}): "
                           f"{_fold(r.missing_items)}")
            if base_row is not None and r.wafers != base_row.wafers:
                out.append(f"[커버리지] {r.lot} wafer {r.wafers}장 "
                           f"(기준 {base_row.wafers}장)")
            if r.cond_missing:
                out.append(f"[커버리지] {r.lot}에 없는 측정 조건 "
                           f"{len(r.cond_missing)}건: {_fold(r.cond_missing)}")
            if r.cond_extra:
                out.append(f"[커버리지] {r.lot}에만 있는 측정 조건 "
                           f"{len(r.cond_extra)}건: {_fold(r.cond_extra)}")
            if r.point_off:
                out.append(f"[커버리지] {r.lot} wafer당 포인트 중앙값이 "
                           f"기준의 {r.point_ratio:.2f}배")
        return out


def build(state) -> CoverageReport:
    """AppState의 분석 프레임 → CoverageReport. DB를 다시 조회하지 않는다."""
    df = getattr(state, "data", None)
    if df is None or df.is_empty() or "lot" not in df.columns:
        return CoverageReport()
    from etreport.data.loader import item_columns
    return build_frame(df, item_columns(df))


def build_frame(df: pl.DataFrame, items: list[str]) -> CoverageReport:
    """프레임과 item 목록만으로 계산 — 테스트가 상태 없이 부를 수 있게 나눠 둔다."""
    lots = [str(v) for v in dict.fromkeys(df["lot"].to_list()) if v is not None]
    if not lots:
        return CoverageReport()

    rows: list[LotRow] = []
    present: dict[str, set[str]] = {}
    conds: dict[str, set[str]] = {}
    for lot in lots:
        sub = df.filter(pl.col("lot").cast(pl.Utf8) == lot)
        # item별 "값이 하나라도 있는가"를 한 번에 센다 — item이 1000개인 실측
        # 규모에서 컬럼마다 따로 훑으면 눈에 띄게 느려진다.
        have: set[str] = set()
        if items:
            counts = sub.select(
                [pl.col(c).is_not_null().sum().alias(c) for c in items]).row(0)
            have = {c for c, n in zip(items, counts) if n}
        present[lot] = have

        cond = set()
        if {"step", "temp", "site"} <= set(sub.columns):
            cond = {_cond_label(*r) for r in
                    sub.select(["step", "temp", "site"]).unique().iter_rows()}
        conds[lot] = cond

        # wafer 컬럼이 없는 스키마(로더를 거치지 않은 프레임)에서도 죽지 않는다
        has_wafer = "wafer" in sub.columns
        per_wafer = (sub.group_by("wafer").len()["len"] if has_wafer
                     else pl.Series("len", [sub.height]))
        rows.append(LotRow(
            lot=lot,
            wafers=sub["wafer"].n_unique() if has_wafer else 0,
            items=len(have),
            points_median=float(per_wafer.median() or 0.0),
            conditions=sorted(cond),
        ))

    # 기준 lot — item이 가장 많은 lot. 동률이면 lot ID 오름차순으로 정해, 같은 DB를
    # 다시 열어도 기준이 흔들리지 않게 한다.
    base = sorted(rows, key=lambda r: (-r.items, r.lot))[0]
    base.is_base = True
    rep = CoverageReport(base_lot=base.lot, rows=rows)
    for r in rows:
        if r.is_base:
            continue
        r.missing_items = sorted(present[base.lot] - present[r.lot])
        r.cond_missing = sorted(conds[base.lot] - conds[r.lot])
        r.cond_extra = sorted(conds[r.lot] - conds[base.lot])
        r.point_ratio = (r.points_median / base.points_median
                         if base.points_median else None)
    return rep
