#!/usr/bin/env python3
"""사내 PC에서 리포메터를 실제로 돌려보기 위한 testset 생성기.

bdq 추출 없이, 추출 결과와 **같은 모양의** rawdata(long parquet)와 그에 맞는
리포메터 엑셀을 만들어 둔다. 그 다음 앱에서 그 리포메터를 열어 값이 맞는지
눈으로 확인하면 된다.

사용 예
-------
    # 1일 20만 행 · item 1000개 · ADDP 40개
    myenv/bin/python tools/make_testset.py --out C:\\temp\\ettest

    # 일주일치 + DuckDB 적재까지 한 번에 (리포메팅은 앱과 같은 코드로 수행)
    python tools/make_testset.py --out C:\\temp\\ettest --days 7 --load

만들어지는 것
-------------
    raw_YYYYMMDD_*.parquet   추출 결과와 동일 스키마의 long rawdata
    reformatter_TEST.xlsx    리포메터 (xlwings가 있을 때. 없으면 .csv)
    expected.csv             정답표 — (키, ALIAS) → 값. 행 단위 엔진으로 계산
    et_test.duckdb           --load 를 줬을 때만

검증 방법
---------
`expected.csv`는 벡터 경로를 타지 않는 **행 단위 엔진**으로 만든 값이다.
앱에서 나온 값(Summary 표 복사 또는 SQL 내보내기)과 이 파일을 비교하면
리포메터가 제대로 도는지 숫자로 확인할 수 있다.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import polars as pl  # noqa: E402

from etreport.data.reformatter import (  # noqa: E402
    Reformatter,
    apply as rf_apply,
    compile_formula,
)
from tests.factory import make_long, make_reformatter, rules_frame  # noqa: E402


def write_reformatter(rf: Reformatter, out: Path) -> Path:
    """리포메터 시트를 xlsx로. xlwings가 없으면 csv로 떨어뜨린다."""
    frame = rules_frame(rf.rules)
    xlsx = out / "reformatter_TEST.xlsx"
    try:
        import xlwings as xw
    except ImportError:
        csv = xlsx.with_suffix(".csv")
        frame.write_csv(csv)
        print(f"  xlwings 없음 → CSV로 저장: {csv}")
        print("    (사내 PC에서 엑셀로 열어 xlsx로 저장한 뒤 앱에서 지정하세요)")
        return csv

    app = xw.App(visible=False, add_book=False)
    try:
        wb = app.books.add()
        sht = wb.sheets[0]
        sht.name = "REFORMATTER"
        sht.range((1, 1)).value = [frame.columns, *frame.rows()]
        sht.autofit("c")
        wb.save(str(xlsx))
        wb.close()
    finally:
        app.quit()
    print(f"  리포메터: {xlsx}")
    return xlsx


def write_expected(rf: Reformatter, long_df: pl.DataFrame, out: Path) -> Path:
    """행 단위 엔진으로 만든 정답표 (앱 결과와 대조용)."""
    key = ["root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos", "step_id"]
    scaled = rf_apply(rf, long_df)          # 벡터 경로 결과 (형태만 빌린다)
    wide = scaled.pivot(on="item_id", index=key, values="value",
                        aggregate_function="first")

    rows = []
    for r in wide.iter_rows(named=True):
        env = {k: v for k, v in r.items() if k not in key}
        for rule in rf.addps():             # ADDP만 행 단위로 다시 계산
            v = compile_formula(rule.formula)(env)
            if v is not None and rule.absolute:
                v = abs(v)
            rows.append({**{k: r[k] for k in key}, "ALIAS": rule.alias,
                         "expected": v, "formula": rule.formula})
    exp = pl.DataFrame(rows)
    p = out / "expected.csv"
    exp.write_csv(p)
    print(f"  정답표: {p}  ({exp.height:,}행 — ADDP {len(rf.addps())}개)")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ET Report testset 생성기")
    ap.add_argument("--out", required=True, help="저장할 폴더")
    ap.add_argument("--items", type=int, default=1000, help="REAL item 수")
    ap.add_argument("--addp", type=int, default=40, help="ADDP item 수")
    ap.add_argument("--days", type=int, default=1, help="며칠치")
    ap.add_argument("--wafers", type=int, default=25)
    ap.add_argument("--chips", type=int, default=8, help="wafer당 측정 포인트")
    ap.add_argument("--start", default="2026-08-01")
    ap.add_argument("--load", action="store_true",
                    help="리포메팅 후 DuckDB(et_test.duckdb)까지 적재")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    d0 = date.fromisoformat(a.start)

    rf = make_reformatter(n_real=a.items, n_addp=a.addp, seed=7)
    print(f"리포메터  REAL {len(rf.reals())} · ADDP {len(rf.addps())}")
    write_reformatter(rf, out)

    itemids = [r.itemid for r in rf.reals()]
    files: list[Path] = []
    first: pl.DataFrame | None = None
    for i in range(a.days):
        t = time.monotonic()
        day = d0 + timedelta(days=i)
        df = make_long(itemids, lots=1, wafers=a.wafers, chips=a.chips,
                       days=1, seed=100 + i, start=day)
        p = out / f"raw_{day:%Y%m%d}_{day:%Y%m%d}_test.parquet"
        df.write_parquet(p)
        files.append(p)
        first = first if first is not None else df
        print(f"  rawdata {i + 1}/{a.days}  {df.height:,}행 → {p.name}"
              f"  ({time.monotonic() - t:.1f}초)")

    assert first is not None
    write_expected(rf, first, out)

    if a.load:
        from etreport.data.db import Store, pivot_and_load
        t = time.monotonic()
        reformatted: list[Path] = []
        for p in files:
            outp = p.with_name(p.stem + "_rf.parquet")
            rf_apply(rf, pl.read_parquet(p)).write_parquet(outp)
            reformatted.append(outp)
        print(f"  리포메팅 완료  ({time.monotonic() - t:.1f}초)")

        t = time.monotonic()
        dbp = out / "et_test.duckdb"
        n = pivot_and_load(Store(dbp), reformatted)
        print(f"  DuckDB 적재  {n:,}행(wide) → {dbp}  ({time.monotonic() - t:.1f}초)")

    print("\n다음 단계: 앱 [분석] 도크에서 위 리포메터(와 DuckDB)를 지정하고 [적용]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
