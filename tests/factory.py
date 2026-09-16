"""합성 testset 생성기 — 사내 실측 규모를 그대로 재현한다.

기준(사용자 실측): rawdata(long) **1일 20만 행**, item **1000여 개**.
  → item 1000개 × 키 200개(lot 1 · wafer 25 · chip 8) = 200,000행 / 일

여기서 만든 프레임은 `data/extractor.ARROW_SCHEMA`와 컬럼·타입이 같아서
추출 결과 parquet 대신 그대로 `reformatter.apply()` → `db.pivot_and_load()`에
넣을 수 있다. 즉 bdq도 Excel도 없이 파이프라인 전체를 돌릴 수 있다.

값에는 일부러 함정을 섞는다 — NULL(미측정), 0(나눗셈 분모), 음수(Sqrt/Log 정의역).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import polars as pl

from etreport.data.reformatter import COLUMNS, Reformatter, Rule, validate

# 실제 ET 리포메터의 열 이름 그대로 (COLUMNS와 순서를 맞춘다)
assert COLUMNS[:3] == ["CATEGORY", "ITEMID", "ALIAS"]


def real_itemid(i: int) -> str:
    return f"ET_PARAM_{i:04d}"


def real_alias(i: int) -> str:
    return f"IT{i:04d}"


# ADDP 수식 형태 — 실사용에서 나오는 모양들.
# %0%,%1%…은 앞서 정의된 ALIAS 자리 (`fill()`이 {ALIAS} 형태로 바꾼다).
ADDP_SHAPES: list[str] = [
    "%0%-%1%",                       # 델타
    "(%0%+%1%)/2",                   # 평균
    "%0%/%1%",                       # 비 — 분모 0이면 NULL이어야 한다
    "Abs(%0%-%1%)",
    "Std(%0%,%1%,%2%,%3%)",          # 그룹 산포 (6키 묶음 표본 n-1, NULL 제외)
    "Avg(%0%,%1%,%2%)",
    "Max(%0%,%1%)-Min(%0%,%1%)",
    "Sqrt(Abs(%0%))",
    "Log10(Abs(%0%)+1)",
    "Ln(Abs(%0%)+1)",
    "(%0%*2-%1%)/(%2%+1e-9)",
    "Sum(%0%,%1%,%2%)/3",
]


def fill(shape: str, *aliases: str) -> str:
    """'%0%-%1%' + ('A','B') → '{A}-{B}'  (ADDP FORM의 실제 표기)."""
    out = shape
    for i, a in enumerate(aliases):
        out = out.replace(f"%{i}%", "{" + a + "}")
    return out


def make_rules(n_real: int = 20, n_addp: int = 6, seed: int = 0,
               chain: bool = True) -> list[Rule]:
    """REAL n개 + ADDP m개. ADDP는 항상 **자기보다 위** 행만 참조한다.

    chain=True면 마지막 ADDP가 앞의 ADDP를 참조한다(ADDP-on-ADDP).
    """
    rng = np.random.default_rng(seed)
    rules: list[Rule] = []
    for i in range(n_real):
        rules.append(Rule(
            category="REAL",
            itemid=real_itemid(i),
            alias=real_alias(i),
            absolute=bool(i % 7 == 0),          # 일부만 절대값
            scale=float([1.0, 1e3, 1e-3, 1e6][i % 4]),
            formula="",
            unit="V" if i % 2 else "A",
            speclow=float(rng.uniform(-2, 0)),
            spechigh=float(rng.uniform(1, 3)),
            target=0.5,
            row=i + 2,
        ))
    for j in range(n_addp):
        shape = ADDP_SHAPES[j % len(ADDP_SHAPES)]
        refs = [real_alias((j * 3 + k) % n_real) for k in range(4)]
        if chain and j == n_addp - 1 and j > 0:
            refs[0] = f"ADDP{j - 1:02d}"        # 앞 ADDP 참조
        rules.append(Rule(
            category="ADDP",
            itemid="",
            alias=f"ADDP{j:02d}",
            absolute=bool(j % 5 == 0),
            scale=1.0,
            formula=fill(shape, *refs),
            unit="",
            speclow=None,
            spechigh=float(j + 1),
            target=None,
            row=n_real + j + 2,
        ))
    return rules


def make_reformatter(n_real: int = 20, n_addp: int = 6, seed: int = 0,
                     chain: bool = True) -> Reformatter:
    """검증까지 마친 Reformatter (Excel 없이)."""
    rf = Reformatter(rules=make_rules(n_real, n_addp, seed, chain))
    validate(rf)
    return rf


def rules_frame(rules: list[Rule]) -> pl.DataFrame:
    """리포메터 시트 모양의 DataFrame — xlio.read_sheet가 돌려주는 것과 같은 형태.

    xlwings는 숫자 셀을 float으로, 빈 칸을 None으로 준다. 문자열 열에 숫자가
    섞여 들어오는 경우까지 재현하려고 SPECLOW만 일부러 문자열로 넣는다.
    """
    return pl.DataFrame({
        "CATEGORY": [r.category for r in rules],
        "ITEMID": [r.itemid or None for r in rules],
        "ALIAS": [r.alias for r in rules],
        "ABSOLUTE": ["Y" if r.absolute else "N" for r in rules],
        "SCALE FACTOR": [r.scale for r in rules],
        "ADDP FORM": [r.formula or None for r in rules],
        "UNIT": [r.unit or None for r in rules],
        "SPECLOW": [None if r.speclow is None else f"{r.speclow:.4f}"
                    for r in rules],
        "SPECHIGH": [r.spechigh for r in rules],
        "TARGET": [r.target for r in rules],
    })


def sheet_from_rows(header: list[str], rows: list[list]) -> pl.DataFrame:
    """손으로 적은 표 → 시트 프레임. 열 타입은 xlio.frame_from_rows와 같은 규칙."""
    from etreport.data.xlio import frame_from_rows
    return frame_from_rows([header, *rows])


# ── rawdata (long) ───────────────────────────────────────────
def make_long(itemids: list[str], lots: int = 1, wafers: int = 25,
              chips: int = 8, days: int = 1, seed: int = 0,
              null_rate: float = 0.02, zero_rate: float = 0.01,
              start: date = date(2026, 8, 1)) -> pl.DataFrame:
    """추출 결과와 같은 long 프레임.

    행 수 = len(itemids) × lots × wafers × chips × days
    (기본값 × item 1000개 = 1일 20만 행)
    """
    rng = np.random.default_rng(seed)
    n_key = lots * wafers * chips * days
    n_item = len(itemids)
    n = n_key * n_item

    # 키 축 (n_key개) — 각 item마다 그대로 반복된다
    idx = np.arange(n_key)
    day_i = idx // (lots * wafers * chips)
    rest = idx % (lots * wafers * chips)
    lot_i = rest // (wafers * chips)
    waf_i = (rest // chips) % wafers
    chip_i = rest % chips

    lot = np.array([f"PA{2600 + i:04d}" for i in range(lots)])[lot_i]
    waf = np.array([f"{i + 1:02d}" for i in range(wafers)])[waf_i]
    cx = (chip_i % 4).astype(np.int32) * 10 + 5
    cy = (chip_i // 4).astype(np.int32) * 10 + 5
    t0 = np.datetime64(
        datetime.combine(start, datetime.min.time()) + timedelta(hours=9), "us")
    tkout = (t0 + day_i * np.timedelta64(1, "D")
             + waf_i * np.timedelta64(3, "m")).astype("datetime64[us]")

    # item 축 — item마다 다른 중심값·산포 (실제 ET 파라미터처럼 자릿수가 제각각)
    center = rng.lognormal(mean=0.0, sigma=1.5, size=n_item)
    spread = center * 0.05

    key_rep = np.tile(np.arange(n_key), n_item)          # 0..n_key-1 반복
    item_rep = np.repeat(np.arange(n_item), n_key)

    vals = center[item_rep] + rng.normal(0, 1, n) * spread[item_rep]
    vals[rng.random(n) < 0.10] *= -1.0                   # 음수 섞기
    vals[rng.random(n) < zero_rate] = 0.0                # 분모 0
    vals = vals.astype(np.float64)
    null_mask = rng.random(n) < null_rate

    return pl.DataFrame({
        "line_id": pl.Series(["L1"] * n, dtype=pl.Utf8),
        "root_lot_id": pl.Series(lot[key_rep], dtype=pl.Utf8),
        "wafer_id": pl.Series(waf[key_rep], dtype=pl.Utf8),
        "chip_x_pos": pl.Series(cx[key_rep], dtype=pl.Int32),
        "chip_y_pos": pl.Series(cy[key_rep], dtype=pl.Int32),
        "temperature": pl.Series(np.full(n, 25.0), dtype=pl.Float64),
        "step_id": pl.Series(["M2"] * n, dtype=pl.Utf8),
        "step_seq": pl.Series(np.full(n, 1, dtype=np.int32), dtype=pl.Int32),
        "total_site_cnt": pl.Series(np.full(n, chips, dtype=np.int32),
                                    dtype=pl.Int32),
        "tkout_time": pl.Series(tkout[key_rep], dtype=pl.Datetime("us")),
        "item_id": pl.Series(np.array(itemids)[item_rep], dtype=pl.Utf8),
        "et_value": pl.Series(np.where(null_mask, np.nan, vals),
                              dtype=pl.Float64).fill_nan(None),
    })


def make_wide(aliases: list[str], n_rows: int = 500, seed: int = 0,
              null_rate: float = 0.15, zero_rate: float = 0.05) -> pl.DataFrame:
    """수식 검증용 wide 프레임 — NULL·0·음수를 넉넉히 섞는다."""
    rng = np.random.default_rng(seed)
    cols = {}
    for a in aliases:
        v = rng.normal(0, 3, n_rows)
        v[rng.random(n_rows) < zero_rate] = 0.0
        s = pl.Series(a, v, dtype=pl.Float64)
        cols[a] = pl.select(
            pl.when(pl.Series(rng.random(n_rows) < null_rate))
            .then(None).otherwise(s).alias(a)).to_series()
    return pl.DataFrame(cols)
