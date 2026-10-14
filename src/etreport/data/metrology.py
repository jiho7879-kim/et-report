"""Inline 계측(`fab.f_fab_wf_met`) — ET 분석에 계측값을 붙여 쓴다.

쓰임새 세 가지(기능 B):
  1. 사용자가 준 `step_id`+`item_id` 목록의 계측값을 **분석 중인 lot에 한해**
     가져와 (lot, wafer)로 ET 데이터에 붙인다 → 요약 표·산점도 x축
  2. subitem 처리 규칙(확정):
       site level  — `RANGE·STD·MIN·VALUE·SLOTID·Q2·MAX`를 **제외한** 나머지
       wafer level — `Q2`
  3. 분석 대상 lot 안에서 전체 계측 item과 ET item의 관계를 통계로 훑어
     **유의미한 top-k 인자**를 뽑는다(PPT 리포트로 나간다)

집계·그리기는 새로 만들지 않는다 — wafer 집계는 `model/aggregate.py`,
그림은 `render/mpl_renderer.py`, 덱은 `render/pptgen.py`를 그대로 쓴다.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import date, timedelta

import polars as pl

log = logging.getLogger(__name__)

TABLE = "fab.f_fab_wf_met"
DEFAULT_LINE = "KFBK"

#: 원본 컬럼명 그대로.
COLUMNS: list[str] = [
    "root_lot_id", "wafer_id", "step_id", "item_id", "subitem_id",
    "fab_value", "line_id", "tkout_time",
]

#: site level에서 **빼는** subitem (나머지가 site 값)
SITE_EXCLUDE = ("RANGE", "STD", "MIN", "VALUE", "SLOTID", "Q2", "MAX")
#: wafer level 대표값
WAFER_SUBITEM = "Q2"
#: 통계 분석 대상 item 패턴(확정)
ITEM_REGEX = "CD|THK|DEPTH|TIP|RCS"


def build_met_sql(lots: list[str], steps: list[str] | None = None,
                  items: list[str] | None = None,
                  line_id: str = DEFAULT_LINE,
                  item_regex: str | None = None,
                  table: str = TABLE,
                  d_from: date | None = None,
                  d_to: date | None = None) -> str:
    """계측 조회 SQL — **분석 대상 lot으로 반드시 좁힌다**.

    lot을 비우면 전체 스캔이 되므로 호출측이 항상 lot을 준다(ET 추출에서
    `item_id IN (...)`을 강제하는 것과 같은 이유).

    기간을 주면 `tkout_time`으로도 좁힌다. 기본값은 화면이 정하는데, 분석 중인
    lot의 ET tkout_time 기준 180일 이전부터다(`data/lotcontext.py`) — 계측은 ET
    보다 앞선 공정에서 찍히므로 그 뒤를 볼 이유가 없고, 같은 lot 이름이 예전에도
    쓰였다면 기간 없이는 옛날 값이 섞인다. **비우면 조건을 걸지 않는다**(예전과 동일).
    """
    if not lots:
        raise ValueError("분석 대상 lot이 필요합니다 (전체 스캔 방지)")

    def _in(col: str, vals: list[str]) -> str:
        quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in vals)
        return f"{col} IN ({quoted})"

    where = [f"line_id = '{line_id}'", _in("root_lot_id", lots)]
    if steps:
        where.append(_in("step_id", steps))
    if items:
        where.append(_in("item_id", items))
    if item_regex:
        where.append(f"item_id REGEXP '{item_regex}'")
    if d_from is not None:
        where.append(f"tkout_time >= '{d_from:%Y-%m-%d} 00:00:00'")
    if d_to is not None:
        hi = d_to + timedelta(days=1)
        where.append(f"tkout_time <  '{hi:%Y-%m-%d} 00:00:00'")
    body = "\n  AND  ".join(where)
    cols = ", ".join(COLUMNS)
    return f"SELECT {cols}\nFROM   {table}\nWHERE  {body}"


def fetch(sql: str) -> pl.DataFrame:
    import bigdataquery as bdq  # 사내 패키지 — 지연 import

    from etreport.data.extractor import normalize_categoricals
    return normalize_categoricals(pl.from_pandas(bdq.getData(sql)))


# ── subitem 규칙 ─────────────────────────────────────────────
def site_rows(df: pl.DataFrame) -> pl.DataFrame:
    """site level — 요약 subitem을 뺀 나머지(측정 지점별 값)."""
    if df.is_empty():
        return df
    up = pl.col("subitem_id").cast(pl.Utf8).str.to_uppercase()
    return df.filter(up.is_null() | ~up.is_in(list(SITE_EXCLUDE)))


def wafer_rows(df: pl.DataFrame) -> pl.DataFrame:
    """wafer level — Q2(중앙값) 한 점."""
    if df.is_empty():
        return df
    up = pl.col("subitem_id").cast(pl.Utf8).str.to_uppercase()
    return df.filter(up == WAFER_SUBITEM)


def wafer_values(df: pl.DataFrame, level: str = "wafer") -> pl.DataFrame:
    """(lot, wafer, 계측이름) → 값 하나로 정리.

    계측이름은 `step_id::item_id`로 만든다 — 서로 다른 step의 같은 item이
    한 열로 뭉치면 안 된다. site level은 wafer 안 여러 점의 평균을 쓴다
    (ET 쪽 wafer 집계와 같은 결).
    """
    src = wafer_rows(df) if level == "wafer" else site_rows(df)
    if src.is_empty():
        return pl.DataFrame(schema={"lot": pl.Utf8, "wafer": pl.Utf8,
                                    "met": pl.Utf8, "value": pl.Float64})
    return (src
            .with_columns(
                (pl.col("step_id").cast(pl.Utf8) + "::"
                 + pl.col("item_id").cast(pl.Utf8)).alias("met"),
                pl.col("fab_value").cast(pl.Float64, strict=False).alias("value"))
            .group_by(["root_lot_id", "wafer_id", "met"])
            .agg(pl.col("value").mean().alias("value"))
            .rename({"root_lot_id": "lot", "wafer_id": "wafer"})
            .sort(["lot", "wafer", "met"]))


def to_wide(df: pl.DataFrame, level: str = "wafer") -> pl.DataFrame:
    """(lot, wafer) × 계측 열 — ET 프레임에 붙이기 좋은 모양."""
    vals = wafer_values(df, level)
    if vals.is_empty():
        return pl.DataFrame(schema={"lot": pl.Utf8, "wafer": pl.Utf8})
    return vals.pivot(on="met", index=["lot", "wafer"], values="value",
                      aggregate_function="first").sort(["lot", "wafer"])


def attach(data: pl.DataFrame, met: pl.DataFrame,
           level: str = "wafer") -> tuple[pl.DataFrame, list[str]]:
    """ET 분석 프레임에 계측 열을 붙인다. (프레임, 붙은 계측 이름들) 반환.

    조인 키는 (lot, wafer) — 계측은 wafer 대표값이라 die 좌표가 없다.
    **표기를 정규화해서 붙인다**(`model/wafers`) — 계측 테이블은 `01`,
    ET DB는 `W01`로 적히는 일이 흔해서 그대로 조인하면 전부 null이 된다.
    같은 이름의 열이 이미 있으면 덮어쓰지 않고 건너뛴다(§오류 처리: 조용히
    바꾸지 않는다).
    """
    from etreport.model import wafers

    wide = to_wide(met, level)
    if data is None or wide.is_empty():
        return data, []
    names = [c for c in wide.columns if c not in ("lot", "wafer")]
    fresh = [c for c in names if c not in data.columns]
    if not fresh:
        return data, []
    keys = [wafers.lot_key_expr("lot").alias("_lk"),
            wafers.wafer_key_expr("wafer").alias("_wk")]
    right = wide.select(["lot", "wafer", *fresh]).with_columns(keys) \
                .drop(["lot", "wafer"]) \
                .unique(subset=["_lk", "_wk"], keep="first")
    joined = (data.with_columns(keys)
              .join(right, on=["_lk", "_wk"], how="left")
              .drop(["_lk", "_wk"]))
    return joined, fresh


# ── top-k 유의 인자 ──────────────────────────────────────────
def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


#: 표본(wafer)이 이보다 적으면 모델을 세우지 않는다 — 나무 몇 그루가 외운 값이 된다.
MIN_WAFERS = 6
#: 빈 칸이 이 비율을 넘는 계측 인자는 뺀다(대부분을 메워 넣은 값으로 순위를 매기게 된다).
MAX_MISSING = 0.5


def _models() -> dict[str, object]:
    """투표에 들어가는 모델 — 이름 → 회귀기. xgboost는 설치돼 있을 때만 낀다.

    전부 나무 모델이라 단위·비선형·상호작용에 둔감하고, `feature_importances_`로
    같은 잣대의 중요도를 낸다. 시드를 고정해 같은 데이터면 같은 순위다.
    """
    from sklearn.ensemble import (
        ExtraTreesRegressor,
        GradientBoostingRegressor,
        RandomForestRegressor,
    )
    out: dict[str, object] = {
        "RF": RandomForestRegressor(n_estimators=300, random_state=0, n_jobs=-1),
        "ET": ExtraTreesRegressor(n_estimators=300, random_state=0, n_jobs=-1),
        "GB": GradientBoostingRegressor(n_estimators=200, max_depth=3,
                                        random_state=0),
    }
    try:
        from xgboost import XGBRegressor
        out["XGB"] = XGBRegressor(n_estimators=200, max_depth=3, random_state=0,
                                  n_jobs=1, verbosity=0)
    except ImportError:
        pass
    return out


def vote_factors(data: pl.DataFrame, met_names: list[str], y_item: str,
                 excluded: set[str] | None = None, k: int = 10,
                 stats=None) -> pl.DataFrame:
    """ET item 하나(`y_item`)를 가장 잘 설명하는 계측 인자 — 모델 여럿의 **투표**.

    wafer 집계값(`model/aggregate.wafer_stats`, 화면·표·PPT와 같은 숫자)으로
    표본 = wafer, 입력 = 계측 인자, 정답 = `y_item`인 회귀를 모델마다 세우고,
    중요도 순위를 매긴 뒤 **평균 순위(Borda)**로 합친다. `votes`는 그 인자를
    top-k에 올린 모델 수다 — 한 모델만 좋아하는 인자와 모두가 고른 인자를 가른다.

    lot이 둘 이상이면 x·y 모두 **lot 평균을 빼고** 학습한다. 그대로 두면 lot
    사이의 평균 차이만으로 lot을 가르는 인자가 1등이 된다("lot 효과") — 예전
    상관 순위가 lot 내 상관(`r_within`)을 함께 보던 것과 같은 이유다.

    `r`(피어슨, 같은 lot 보정 후)은 순위에 쓰지 않고 방향을 읽으라고 함께 낸다.
    scikit-learn이 없으면 `ImportError`를 그대로 올린다(창이 안내한다).
    """
    from etreport.model.aggregate import wafer_stats

    empty = {"rank": pl.Int64, "met": pl.Utf8, "item": pl.Utf8,
             "votes": pl.Int64, "mean_rank": pl.Float64, "r": pl.Float64,
             "n": pl.Int64}
    if data is None or data.is_empty() or not met_names or not y_item:
        return pl.DataFrame(schema=empty)
    if stats is None:
        names = [c for c in {*met_names, y_item} if c in data.columns]
        stats = wafer_stats(data, excluded or set(), names, "avg")
    rows = [{"lot": str(lot), **vals} for (lot, _w), vals in stats.values.items()
            if vals.get(y_item) is not None]
    if len(rows) < MIN_WAFERS:
        return pl.DataFrame(schema=empty)
    feats = [m for m in dict.fromkeys(met_names) if m != y_item]
    df = pl.DataFrame(rows, schema={"lot": pl.Utf8, y_item: pl.Float64,
                                    **dict.fromkeys(feats, pl.Float64)})
    feats = [m for m in feats
             if df[m].null_count() <= MAX_MISSING * df.height
             and (df[m].drop_nulls().std() or 0) > 0]
    if not feats:
        return pl.DataFrame(schema=empty)
    cols = [y_item, *feats]
    if df["lot"].n_unique() > 1:                       # lot 효과를 뺀다
        df = df.with_columns([(pl.col(c) - pl.col(c).mean().over("lot")).alias(c)
                              for c in cols])
    # 빈 칸은 그 인자의 중앙값 — 나무 모델 셋이 같은 입력을 보게 한다
    df = df.with_columns([pl.col(c).fill_null(pl.col(c).median()) for c in feats])
    x = df.select(feats).to_numpy()
    y = df[y_item].to_numpy()

    import numpy as np
    ranks: dict[str, np.ndarray] = {}
    for name, model in _models().items():
        model.fit(x, y)
        imp = np.asarray(model.feature_importances_, dtype=float)
        # 1 = 가장 중요. 동률은 같은 순위의 평균이 아니라 순서대로 — 결정적이면 된다
        ranks[name] = np.argsort(np.argsort(-imp, kind="stable")) + 1
    top = min(k, len(feats))
    mean_rank = np.mean(list(ranks.values()), axis=0)
    votes = sum((r <= top).astype(int) for r in ranks.values())
    out = pl.DataFrame({
        "met": feats, "item": [y_item] * len(feats),
        "votes": votes.astype(np.int64), "mean_rank": mean_rank,
        "r": [_pearson(df[m].to_list(), df[y_item].to_list()) for m in feats],
        "n": [int(df.height)] * len(feats),
        **{f"rank_{n}": r.astype(np.int64) for n, r in ranks.items()}},
        schema_overrides={"r": pl.Float64})
    return (out.sort(["mean_rank", "votes", "met"], descending=[False, True, False])
            .head(k).with_row_index("rank", offset=1)
            .with_columns(pl.col("rank").cast(pl.Int64)))


def match_regex(items: list[str], pattern: str = ITEM_REGEX) -> list[str]:
    """통계 분석에 올릴 item만 — `CD|THK|DEPTH|TIP|RCS`(확정)."""
    rx = re.compile(pattern, re.IGNORECASE)
    return [i for i in items if rx.search(i)]


def parse_pairs(text: str) -> list[tuple[str, str]]:
    """붙여넣은 `step_id  item_id` 목록 → [(step, item), …].

    엑셀에서 두 열을 복사하면 탭으로 붙는다. 머리글이 있으면 건너뛴다.
    """
    out: list[tuple[str, str]] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        cells = [c.strip() for c in re.split(r"[\t,;]", line) if c.strip()]
        if len(cells) < 2:
            continue
        if cells[0].lower() in ("step_id", "step"):      # 머리글
            continue
        out.append((cells[0], cells[1]))
    return out
