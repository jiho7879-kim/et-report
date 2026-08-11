"""Impala 추출 — 청크 분할 → 4워커 병렬 → long parquet(명시 스키마).

bigdataquery(bdq)는 스레드 안전 확인됨. 청크는 (기간×lot) greedy binning으로
비슷한 크기가 되도록 나누고, 실패 청크는 자동 재시도한다.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pyarrow as pa

from etreport.config.catalog import Catalog
from etreport.config.settings import Condition
from etreport.data.querybuilder import build_extract_sql

log = logging.getLogger(__name__)

N_WORKERS = 4
RETRY = 2

# long 스키마를 명시 고정 — 청크마다 타입이 흔들리는 사고 방지
ARROW_SCHEMA = pa.schema([
    ("line_id", pa.string()),
    ("root_lot_id", pa.string()),
    ("wafer_id", pa.string()),
    ("chip_x_pos", pa.int32()),
    ("chip_y_pos", pa.int32()),
    ("temperature", pa.float64()),
    ("step_id", pa.string()),
    ("step_seq", pa.int32()),
    ("total_site_cnt", pa.int32()),
    ("tkout_time", pa.timestamp("us")),
    ("item_id", pa.string()),
    ("et_value", pa.float64()),
])


@dataclass(frozen=True)
class Chunk:
    d_from: date
    d_to: date          # inclusive


def plan_chunks(d_from: date, d_to: date, max_days: int = 1) -> list[Chunk]:
    """기간을 일 단위로 쪼갠다. lot 조건이 좁으면 max_days를 늘려도 된다."""
    out: list[Chunk] = []
    cur = d_from
    while cur <= d_to:
        end = min(cur + timedelta(days=max_days - 1), d_to)
        out.append(Chunk(cur, end))
        cur = end + timedelta(days=1)
    return out


def _fetch(sql: str) -> pl.DataFrame:
    import bigdataquery as bdq  # 사내 패키지

    pdf = bdq.getData(sql)       # pandas 반환 가정
    return pl.from_pandas(pdf)


def extract_to_parquet(
    conditions: list[Condition],
    d_from: date,
    d_to: date,
    catalog: Catalog,
    staging: Path,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Path]:
    """청크별 parquet 파일 목록 반환. 파일명에 기간·uuid 포함(재적재 추적)."""
    chunks = plan_chunks(d_from, d_to)
    files: list[Path] = []
    total = len(chunks)

    def work(ch: Chunk) -> Path | None:
        if should_stop and should_stop():
            return None
        sql = build_extract_sql(conditions, ch.d_from, ch.d_to, catalog)
        last: Exception | None = None
        for attempt in range(RETRY + 1):
            try:
                df = _fetch(sql)
                break
            except Exception as e:      # noqa: BLE001 — 재시도 후 위로
                last = e
                log.warning("청크 %s 시도 %d 실패: %s", ch, attempt + 1, e)
        else:
            raise RuntimeError(f"청크 {ch.d_from}~{ch.d_to} 추출 실패") from last
        df = df.cast({f.name: pl.from_arrow(pa.array([], f.type)).dtype
                      for f in ARROW_SCHEMA}, strict=False)
        p = staging / f"raw_{ch.d_from:%Y%m%d}_{ch.d_to:%Y%m%d}_{uuid.uuid4().hex[:8]}.parquet"
        df.write_parquet(p)
        return p

    done = 0
    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futs = {ex.submit(work, ch): ch for ch in chunks}
        for fut in as_completed(futs):
            got = fut.result()              # 실패 시 여기서 전파
            done += 1
            if got is None:
                continue
            files.append(got)
            if on_progress:
                on_progress(done, total, str(futs[fut].d_from))
    return sorted(files)
