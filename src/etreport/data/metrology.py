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


def _welch_t(a: list[float], b: list[float]) -> float | None:
    """그룹 간 유의차 — Welch t (분산이 달라도 되는 쪽)."""
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return None if se <= 0 else (ma - mb) / se


def top_factors(data: pl.DataFrame, met_names: list[str], et_items: list[str],
                excluded: set[str] | None = None, k: int = 10,
                group_col: str = "gid") -> pl.DataFrame:
    """계측 인자 × ET item의 관계를 훑어 **유의미한 순으로 k개**.

    세 가지를 함께 본다(확정 요구):
      - 상관: wafer 집계값끼리의 피어슨 r (|r|이 클수록 위)
      - **lot 내 상관 `r_within`**: lot마다 따로 구해 wafer 수로 가중 평균한 값
      - 그룹 간 유의차: REF/그 외 그룹으로 나눈 Welch t

    lot을 여러 개 놓고 상관을 구하면 lot 사이의 평균 차이만으로도 r이 커진다
    ("lot 효과"). 그러면 lot 안에서는 아무 관계가 없는 쌍이 상위로 올라온다.
    그래서 **정렬은 둘 중 보수적인 쪽**(|r|과 |r_within| 중 작은 값)으로 한다 —
    두 값이 갈리는 쌍은 화면에서 표식으로 알린다. lot이 하나면 두 값이 같아
    지금까지의 순위 그대로다.

    wafer 집계는 `model/aggregate.wafer_stats`를 쓴다 — 화면·표·PPT와 같은
    숫자여야 한다.
    """
    from etreport.model.aggregate import wafer_stats

    empty = {"met": pl.Utf8, "item": pl.Utf8, "r": pl.Float64,
             "r_within": pl.Float64, "lots": pl.Int64, "n": pl.Int64,
             "t": pl.Float64, "score": pl.Float64}
    if data is None or data.is_empty() or not met_names or not et_items:
        return pl.DataFrame(schema=empty)
    aliases = [c for c in {*met_names, *et_items} if c in data.columns]
    st = wafer_stats(data, excluded or set(), aliases, "avg")
    keys = sorted(st.values)
    groups = {}
    if group_col in data.columns:
        groups = {(lot, wf): g for lot, wf, g in
                  zip(data["lot"], data["wafer"], data[group_col])}

    rows = []
    for met in met_names:
        for item in et_items:
            if met == item or met not in aliases or item not in aliases:
                continue
            pairs = [(st.values[k2].get(met), st.values[k2].get(item))
                     for k2 in keys]
            pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
            if len(pairs) < 3:
                continue
            xs = [x for x, _ in pairs]
            ys = [y for _, y in pairs]
            r = _pearson(xs, ys)
            r_within, n_lots = _within_lot_r(st.values, keys, met, item)
            t = None
            if groups:
                by: dict[str, list[float]] = {}
                for k2 in keys:
                    v = st.values[k2].get(met)
                    g = groups.get(k2, "")
                    if v is not None and g:
                        by.setdefault(g, []).append(v)
                if len(by) >= 2:
                    big = sorted(by.values(), key=len, reverse=True)[:2]
                    t = _welch_t(big[0], big[1])
            # lot 효과로 부풀려진 상관이 위로 올라오지 않게 보수적인 쪽을 쓴다.
            # lot이 하나뿐이면 r_within이 None이라 예전과 같은 값이 된다.
            r_use = abs(r or 0.0) if r_within is None else min(
                abs(r or 0.0), abs(r_within))
            score = max(r_use, min(abs(t or 0.0) / 3.0, 1.0))
            rows.append({"met": met, "item": item, "r": r,
                         "r_within": r_within, "lots": n_lots,
                         "n": len(pairs), "t": t, "score": score})
    if not rows:
        return pl.DataFrame(schema=empty)
    return (pl.DataFrame(rows, schema=empty)
            .sort("score", descending=True).head(k))


def _within_lot_r(values: dict, keys: list, met: str,
                  item: str) -> tuple[float | None, int]:
    """lot 안에서만 구한 상관을 wafer 수로 가중 평균. (값, 쓴 lot 수).

    lot이 하나뿐이면 **None**을 돌려준다 — 합친 상관과 같은 값이므로 따로
    보여 줄 것이 없고, 호출부가 '비교할 수 없음'과 '같음'을 구분하게 된다.
    wafer가 3장 미만인 lot은 뺀다(전체 상관의 기준과 같다).

    함께 돌려주는 수는 **상관을 구할 수 있었던 lot 수**다 — 표의 `lots` 열이
    "몇 개의 lot을 견줘 본 값인가"를 말해야 해석이 된다.
    """
    by_lot: dict[str, list[tuple[float, float]]] = {}
    for k in keys:
        lot = k[0] if isinstance(k, tuple) else k
        x, y = values[k].get(met), values[k].get(item)
        if x is not None and y is not None:
            by_lot.setdefault(str(lot), []).append((x, y))
    usable = {lot: pr for lot, pr in by_lot.items() if len(pr) >= 3}
    if len(usable) < 2:
        return None, len(usable)
    num = den = 0.0
    used = 0
    for pr in usable.values():
        r = _pearson([x for x, _ in pr], [y for _, y in pr])
        if r is None:                     # 한쪽 값이 전부 같으면 상관이 없다
            continue
        num += r * len(pr)
        den += len(pr)
        used += 1
    if not den or used < 2:
        return None, used
    return num / den, used


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
