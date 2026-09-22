"""Tukey(IQR) 이상치 필터 — 표·plot을 그리기 **전에** 걸러 낸다.

규칙은 통상적인 Tukey 울타리다. item마다 사분위수 Q1·Q3를 구하고

    lo = Q1 − k·IQR        hi = Q3 + k·IQR        (IQR = Q3 − Q1)

밖에 있는 측정점을 이상치로 본다. **k는 사용자가 정한다** — 3.0과 4.5가 현장에서
쓰는 값이라 프리셋으로 두되 아무 값이나 넣을 수 있다. boxplot의 수염(1.5)보다
훨씬 크게 잡는 이유는, 여기서 하는 일이 "눈에 띄는 점 표시"가 아니라 **버릴 점
고르기**이기 때문이다. 1.5로 거르면 정상 산포의 꼬리까지 잘려 나간다.

**측정 조건을 섞지 않는다**(`SCOPE_COND`, 기본값). ET는 같은 item을 25 ℃와
125 ℃에서 재고, 두 분포의 중심이 아예 다르다. 하나로 합쳐 사분위수를 구하면
정상적인 고온 측정이 통째로 이상치가 된다. 그래서 `(step, temp)`마다 따로 구한다.
전체로 보고 싶으면 `SCOPE_ALL`.

걸러진 점은 **버리지 않고 기록한다** — 손으로 찍은 제외와 같은 방식으로
사이드카에 남고(`data/exclusions.py`), 화면에는 회색 빈 심볼로 그대로 보인다.
"왜 이 점이 없지"라는 질문이 나오지 않아야 한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import polars as pl

log = logging.getLogger(__name__)

#: 프리셋 배수. 목록에 없는 값도 직접 넣을 수 있다.
PRESET_K: tuple[float, ...] = (3.0, 4.5)
DEFAULT_K = 3.0

#: 사분위수를 어느 범위에서 구할지.
SCOPE_COND = "cond"       # item × (step, temp) — 기본값
SCOPE_ALL = "all"         # item 전체
SCOPE_LABELS = {SCOPE_COND: "측정 조건별 (step · 온도)", SCOPE_ALL: "item 전체"}

#: 사분위수를 믿을 수 있는 최소 표본 수. 이보다 적은 묶음은 거르지 않는다 —
#: 점 4개짜리 묶음에서 IQR을 구하면 멀쩡한 값이 이상치가 된다.
MIN_POINTS = 12

#: 한 번에 처리할 item 수. item이 1000개인 실측 규모에서 표현식을 한꺼번에
#: 2000개 만들면 메모리가 튄다 — 나눠서 돈다(결과는 나누든 말든 같다).
CHUNK = 200


@dataclass
class TukeyConfig:
    """이상치 필터 설정. 설정 파일(AnalysisConfig)에 그대로 저장된다."""
    enabled: bool = False
    k: float = DEFAULT_K
    scope: str = SCOPE_COND

    def label(self) -> str:
        if not self.enabled:
            return "꺼짐"
        return f"{self.k:g}×IQR · {SCOPE_LABELS.get(self.scope, self.scope)}"


@dataclass
class TukeyResult:
    """걸러진 결과. `points`는 제외 사이드카와 같은 모양이다."""
    points: dict[str, dict] = field(default_factory=dict)   # key → {reason,…}
    by_item: dict[str, int] = field(default_factory=dict)   # item → 걸린 점 수
    checked: int = 0                                        # 검사한 점 수
    k: float = DEFAULT_K
    scope: str = SCOPE_COND

    def summary(self) -> str:
        if not self.points:
            return (f"이상치 필터 {self.k:g}×IQR — 걸린 점 없음 "
                    f"(검사 {self.checked:,}점)")
        top = sorted(self.by_item.items(), key=lambda kv: -kv[1])[:3]
        detail = ", ".join(f"{name} {n}" for name, n in top)
        return (f"이상치 필터 {self.k:g}×IQR — {len(self.points):,}점 제외 "
                f"/ 검사 {self.checked:,}점  ({detail}"
                f"{' 외' if len(self.by_item) > 3 else ''})")


def _group_cols(df: pl.DataFrame, scope: str) -> list[str]:
    if scope != SCOPE_COND:
        return []
    return [c for c in ("step", "temp") if c in df.columns]


def find(df: pl.DataFrame, items: list[str], cfg: TukeyConfig) -> TukeyResult:
    """이상치 key를 찾는다. **프레임을 바꾸지 않는다** — 찾기만 한다.

    한 점이 여러 item에서 동시에 이상치일 수 있다. 그때는 **처음 걸린 item**을
    이유로 적는다(모두 적으면 이유 문자열이 끝없이 길어진다). 집계는 `by_item`이
    item마다 따로 세므로 어느 item이 문제인지는 그쪽을 본다.
    """
    res = TukeyResult(k=cfg.k, scope=cfg.scope)
    if df is None or df.is_empty() or not items or "key" not in df.columns:
        return res
    cols = [c for c in items if c in df.columns and df.schema[c].is_numeric()]
    if not cols:
        return res
    res.checked = df.height

    gcols = _group_cols(df, cfg.scope)
    at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scope_note = ("측정 조건별" if gcols else "전체")

    for i in range(0, len(cols), CHUNK):
        batch = cols[i:i + CHUNK]
        bounds = _bounds(df, batch, gcols, cfg.k)
        joined = df.select(["key", *gcols, *batch]).join(
            bounds, on=gcols, how="left") if gcols else df.select(
            ["key", *batch]).join(bounds, how="cross")
        for c in batch:
            bad = joined.filter(
                pl.col(c).is_not_null()
                & pl.col(f"_lo_{c}").is_not_null()
                & ((pl.col(c) < pl.col(f"_lo_{c}"))
                   | (pl.col(c) > pl.col(f"_hi_{c}"))))
            if bad.is_empty():
                continue
            res.by_item[c] = bad.height
            reason = f"Tukey {cfg.k:g}×IQR 초과 — {c} ({scope_note})"
            for key in bad["key"].to_list():
                res.points.setdefault(str(key), {"reason": reason,
                                                 "item": c, "at": at})
    return res


def _bounds(df: pl.DataFrame, cols: list[str], gcols: list[str],
            k: float) -> pl.DataFrame:
    """item마다 (lo, hi). 표본이 `MIN_POINTS` 미만인 묶음은 **NULL**로 둔다.

    NULL이면 그 묶음의 점은 어떤 비교에도 걸리지 않아 그대로 살아남는다 —
    "표본이 적으면 거르지 않는다"를 조건문이 아니라 값으로 표현한 것이다.
    """
    exprs: list[pl.Expr] = []
    for c in cols:
        q1 = pl.col(c).quantile(0.25)
        q3 = pl.col(c).quantile(0.75)
        iqr = q3 - q1
        enough = pl.col(c).drop_nulls().len() >= MIN_POINTS
        exprs += [
            pl.when(enough).then(q1 - iqr * k).otherwise(None)
                .alias(f"_lo_{c}"),
            pl.when(enough).then(q3 + iqr * k).otherwise(None)
                .alias(f"_hi_{c}"),
        ]
    if gcols:
        return df.group_by(gcols).agg(exprs)
    return df.select(exprs)


def apply(state, cfg: TukeyConfig | None = None) -> TukeyResult:
    """상태에 필터를 건다. 결과는 `state.filtered`에 남고 사이드카에 기록된다.

    **끄면 즉시 비운다** — 껐는데 예전에 걸러진 점이 남아 있으면 그 점이 왜
    없는지 알 방법이 없다.
    """
    from etreport.data import exclusions
    from etreport.data.loader import item_columns

    cfg = cfg or getattr(state, "tukey", None) or TukeyConfig()
    state.tukey = cfg
    if state.data is None or not cfg.enabled:
        state.filtered = {}
        if getattr(state, "db_path", ""):
            exclusions.save_filtered(state.db_path, {})
        return TukeyResult(k=cfg.k, scope=cfg.scope)

    res = find(state.data, item_columns(state.data), cfg)
    state.filtered = res.points
    if getattr(state, "db_path", ""):
        exclusions.save_filtered(state.db_path, res.points)
    log.info("%s", res.summary())
    return res


def frame(state) -> pl.DataFrame:
    """걸러진 점의 이력 표 — 창·PPT가 쓴다. (key, item, 이유, 시각)."""
    pts = getattr(state, "filtered", {}) or {}
    return pl.DataFrame({
        "key_hash": list(pts),
        "item": [v.get("item", "") for v in pts.values()],
        "reason": [v.get("reason", "") for v in pts.values()],
        "created_at": [v.get("at", "") for v in pts.values()],
    }, schema={"key_hash": pl.Utf8, "item": pl.Utf8,
               "reason": pl.Utf8, "created_at": pl.Utf8})
