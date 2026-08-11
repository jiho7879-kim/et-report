"""실측 규모 testset — item 1000개 · rawdata(long) 1일 20만 행.

기본 실행에서는 빠진다. 돌리려면:

    myenv/bin/python -m pytest tests/test_bigset.py -m slow -s

`-s`를 붙이면 단계별 실측 시간이 그대로 찍힌다(사내 PC와 비교용).
시간 상한은 회귀 감지용이라 넉넉하게 잡았다 — 값 자체보다 **자릿수**가
바뀌는지를 본다(예: 적재가 초 단위 → 분 단위로 뛰면 실패).
"""
from __future__ import annotations

import time

import polars as pl
import pytest

from etreport.data import db, loader
from etreport.data.reformatter import apply as rf_apply
from etreport.data.reformatter import compile_formula
from tests.factory import make_long, make_reformatter

pytestmark = pytest.mark.slow

N_ITEM = 1000          # 리포메터 REAL item 수 (사용자 실측: 1000여 개)
N_ADDP = 40
WAFERS, CHIPS = 25, 8  # 키 200개/일 → 1000 × 200 = 20만 행/일


@pytest.fixture(scope="module")
def big():
    t = time.monotonic()
    rf = make_reformatter(n_real=N_ITEM, n_addp=N_ADDP, seed=2)
    t_rf = time.monotonic() - t

    t = time.monotonic()
    src = make_long([r.itemid for r in rf.reals()],
                    lots=1, wafers=WAFERS, chips=CHIPS, seed=2, null_rate=0.02)
    t_gen = time.monotonic() - t
    print(f"\n  testset 생성      {src.height:,}행 · item {N_ITEM}  ({t_gen:.1f}초)"
          f"  [리포메터 {t_rf:.1f}초]")
    return rf, src


def test_dataset_shape(big):
    rf, src = big
    assert src.height == N_ITEM * WAFERS * CHIPS == 200_000
    assert src["item_id"].n_unique() == N_ITEM
    assert len(rf.reals()) == N_ITEM and len(rf.addps()) == N_ADDP
    assert src["et_value"].null_count() > 0          # 미측정도 섞여 있다


def test_reformat_one_day(big, caplog):
    """20만 행 리포메팅 — 벡터 경로만 타면 1초 안쪽이어야 한다."""
    rf, src = big
    with caplog.at_level("INFO"):
        t = time.monotonic()
        out = rf_apply(rf, src)
        el = time.monotonic() - t
    print(f"  리포메팅          {src.height:,}행 → {out.height:,}행  ({el:.2f}초)")

    assert "행 단위로 계산" not in caplog.text, "행 단위 폴백이 생겼다 — 수십 배 느려진다"
    assert el < 30, f"리포메팅이 {el:.1f}초 — 벡터 경로가 깨졌는지 확인"

    n_key = WAFERS * CHIPS
    assert out["item_id"].n_unique() == N_ITEM + N_ADDP
    assert out.height <= (N_ITEM + N_ADDP) * n_key
    assert out.height > N_ITEM * n_key * 0.9         # NULL 제외분만 빠진다
    assert out["value"].null_count() == 0


def test_values_match_row_engine_on_sampled_keys(big):
    """벡터 결과를 행 단위 엔진과 대조 — 키 30개 × ADDP 40개."""
    rf, src = big
    out = rf_apply(rf, src)
    key = ["root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos"]
    wide = out.pivot(on="item_id", index=key, values="value",
                     aggregate_function="first").head(30)

    checked = 0
    for r in wide.iter_rows(named=True):
        env = {k: v for k, v in r.items() if k not in key}
        for rule in rf.addps():
            expect = compile_formula(rule.formula)(env)
            if expect is not None and rule.absolute:
                expect = abs(expect)
            got = r.get(rule.alias)
            if expect is None:
                assert got is None, (rule.alias, rule.formula, got)
            else:
                assert got == pytest.approx(expect, rel=1e-9, abs=1e-12), \
                    (rule.alias, rule.formula)
            checked += 1
    print(f"  값 대조           {checked:,}건 일치")
    assert checked == 30 * N_ADDP


def test_full_pipeline_one_day(big, tmp_path, appdata):
    """리포메팅 → parquet → DuckDB 적재 → 분석 화면 로딩까지."""
    from etreport.model.state import AppState

    rf, src = big
    out = rf_apply(rf, src)
    p = tmp_path / "raw_20260801_20260801_big.parquet"
    out.write_parquet(p)

    t = time.monotonic()
    store = db.Store(tmp_path / "big.duckdb")
    n = db.pivot_and_load(store, [p])
    el_load = time.monotonic() - t
    store.con.close()
    print(f"  DuckDB 적재       {n:,}행(wide)  ({el_load:.1f}초)"
          f"  · 버킷 {db.plan_buckets(WAFERS * CHIPS, N_ITEM + N_ADDP)}개")
    # 이력: 버킷 512개 고정 + 버킷마다 key_hash 재계산 → 195초,
    #       재계산만 걷어내고 512개 유지 → 27초, 버킷 수를 데이터에 맞춤 → 1.4초.
    assert el_load < 60, f"적재가 {el_load:.0f}초 — 버킷 수·key_hash 재계산 회귀 의심"
    assert n == WAFERS * CHIPS

    t = time.monotonic()
    st = AppState()
    summary = loader.load_state(st, str(tmp_path / "big.duckdb"))
    el_open = time.monotonic() - t
    print(f"  분석 로딩         {summary}  ({el_open:.1f}초)")

    assert st.data.height == WAFERS * CHIPS
    items = loader.item_columns(st.data)
    assert len(items) == N_ITEM + N_ADDP
    assert el_open < 60

    # 값이 끝까지 살아 있는지 — ADDP 하나를 long 결과와 대조
    alias = rf.addps()[0].alias
    expect = out.filter(pl.col("item_id") == alias)["value"].sort().to_list()
    got = st.data[alias].drop_nulls().sort().to_list()
    assert got == pytest.approx(expect)


def test_seven_days_load(big, tmp_path):
    """일주일치(140만 행) 적재 — 실사용에서 가장 무거운 축."""
    from datetime import date, timedelta

    rf, _ = big
    itemids = [r.itemid for r in rf.reals()]
    files = []
    for d in range(7):
        day = date(2026, 8, 1) + timedelta(days=d)
        src = make_long(itemids, lots=1, wafers=WAFERS, chips=CHIPS,
                        seed=200 + d, start=day)
        p = tmp_path / f"raw_{day:%Y%m%d}_{day:%Y%m%d}.parquet"
        rf_apply(rf, src).write_parquet(p)
        files.append(p)

    t = time.monotonic()
    n = db.pivot_and_load(db.Store(tmp_path / "week.duckdb"), files)
    el = time.monotonic() - t
    print(f"  7일 적재          {n:,}행(wide)  ({el:.1f}초)")
    assert n == 7 * WAFERS * CHIPS
    assert el < 120


def test_seven_days_reformatting(big):
    """일주일치(140만 행) — 청크(=파일)마다 부르는 실제 사용 형태."""
    rf, _ = big
    itemids = [r.itemid for r in rf.reals()]
    total_in = total_out = 0
    t = time.monotonic()
    for day in range(7):
        src = make_long(itemids, lots=1, wafers=WAFERS, chips=CHIPS,
                        days=1, seed=100 + day)
        out = rf_apply(rf, src)
        total_in += src.height
        total_out += out.height
    el = time.monotonic() - t
    print(f"  7일 리포메팅      {total_in:,}행 → {total_out:,}행  ({el:.1f}초)")
    assert total_in == 7 * 200_000
    assert el < 120
