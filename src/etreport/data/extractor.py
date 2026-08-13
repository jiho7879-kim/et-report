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
from etreport.data.querybuilder import ITEM_ID_CHUNK, build_extract_sql

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


@dataclass(frozen=True)
class Unit:
    """실행 단위 하나 = 기간 청크 × item 그룹 (§4.2). 이 단위로 병렬 실행한다."""
    chunk: Chunk
    group: int                      # 0-based item 그룹 번호
    n_groups: int
    item_ids: list[str] | None

    def label(self) -> str:
        """진행 로그용 — '08-04 item 2/3'."""
        day = f"{self.chunk.d_from:%m-%d}"
        return day if self.n_groups <= 1 else \
            f"{day} item {self.group + 1}/{self.n_groups}"


def item_groups(item_ids: list[str] | None) -> list[list[str] | None]:
    """item 목록을 상한(9999) 이하 그룹으로 나눈다. 필터가 없으면 그룹 하나."""
    if not item_ids:
        return [None]
    return [item_ids[s:s + ITEM_ID_CHUNK]
            for s in range(0, len(item_ids), ITEM_ID_CHUNK)]


def plan_units(d_from: date, d_to: date,
               item_ids: list[str] | None = None,
               max_days: int = 1) -> list[Unit]:
    """기간 × item 그룹의 **곱**을 실행 단위 목록으로 편다."""
    groups = item_groups(item_ids)
    return [Unit(ch, i, len(groups), ids)
            for ch in plan_chunks(d_from, d_to, max_days)
            for i, ids in enumerate(groups)]


def _fetch(sql: str) -> pl.DataFrame:
    import bigdataquery as bdq  # 사내 패키지

    pdf = bdq.getData(sql)       # pandas 반환 가정
    return pl.from_pandas(pdf)


def _target_dtypes() -> dict[str, pl.DataType]:
    return {f.name: pl.from_arrow(pa.array([], f.type)).dtype for f in ARROW_SCHEMA}


def normalize_categoricals(df: pl.DataFrame) -> pl.DataFrame:
    """Categorical → Utf8, 문자열 시각 → Datetime (§10.4).

    long 고정 스키마가 없는 소스(fab tracking·inline 계측)도 같은 함정을 밟기
    때문에 스키마 고정과 분리해 둔다 — 컬럼명은 원본 그대로 유지한다.
    """
    cat = [c for c, t in zip(df.columns, df.dtypes)
           if t in (pl.Categorical, pl.Enum)]
    if cat:
        df = df.with_columns([pl.col(c).cast(pl.Utf8) for c in cat])
    times = [c for c in df.columns
             if c.endswith(("_time", "_dttm")) and df.schema[c] == pl.Utf8]
    if times:
        df = df.with_columns([pl.col(c).str.to_datetime(time_unit="us",
                                                        strict=False)
                              for c in times])
    return df


def normalize_schema(df: pl.DataFrame) -> pl.DataFrame:
    """bdq 결과를 long 고정 스키마로 맞춘다 — 청크마다 타입이 흔들리지 않게.

    계획서 §10.4의 두 함정을 여기서 막는다.

    1) **Categorical → 숫자 직접 캐스팅은 polars가 막는다**
       (`cannot cast categorical types to Float64`). bdq/pandas를 거치면 반복
       문자열 컬럼이 Categorical로 오므로 Utf8을 한 번 거친다.
    2) **문자열 → Datetime은 cast가 아니라 `str.to_datetime()` 파싱**이어야 한다.
       cast로는 예외도 없이 조용히 전부 null이 되고, tkout_time이 null이면
       key_hash가 뭉쳐 서로 다른 측정이 중복으로 지워진다.

    스키마에 있는데 결과에 없는 컬럼은 null로 채운다. 청크 parquet들을
    `scan_parquet`로 한꺼번에 읽기 때문에 파일마다 컬럼이 다르면 적재가 깨진다.
    """
    target = _target_dtypes()

    cat = [c for c, t in zip(df.columns, df.dtypes)
           if t in (pl.Categorical, pl.Enum)]
    if cat:
        df = df.with_columns([pl.col(c).cast(pl.Utf8) for c in cat])

    parse = [pl.col(c).str.to_datetime(time_unit="us", strict=False).alias(c)
             for c, dt in target.items()
             if c in df.columns and dt == pl.Datetime and df.schema[c] == pl.Utf8]
    if parse:
        df = df.with_columns(parse)

    df = df.cast({c: dt for c, dt in target.items() if c in df.columns},
                 strict=False)

    missing = [c for c in target if c not in df.columns]
    if missing:
        log.warning("조회 결과에 없는 컬럼을 null로 채웁니다: %s", ", ".join(missing))
        df = df.with_columns([pl.lit(None, dtype=target[c]).alias(c)
                              for c in missing])
    rest = [c for c in df.columns if c not in target]
    return df.select([*target, *rest])


def extract_to_parquet(
    conditions: list[Condition],
    d_from: date,
    d_to: date,
    catalog: Catalog,
    staging: Path,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    item_ids: list[str] | None = None,
) -> list[Path]:
    """청크별 parquet 파일 목록 반환. 파일명에 기간·item 그룹·uuid 포함.

    **청크 = 기간 × item 그룹의 곱**(확정 사양 §4.2)이고, 그 곱 전체가 병렬
    대상이다 — item이 3그룹이면 7일치는 7개가 아니라 21개를 4워커가 나눠 문다.
    bdq는 스레드 안전이 확인됐다.

    downstream(pivot_and_load)은 parquet 전체를 scan_parquet로 합치고 key_hash로
    dedup하므로 파일이 여러 개로 갈라져도 안전하다.
    """
    units = plan_units(d_from, d_to, item_ids)
    files: list[Path] = []
    total = len(units)

    def work(u: Unit) -> Path | None:
        if should_stop and should_stop():
            return None
        sql = build_extract_sql(conditions, u.chunk.d_from, u.chunk.d_to,
                               catalog, item_ids=u.item_ids)
        last: Exception | None = None
        for attempt in range(RETRY + 1):
            try:
                df = _fetch(sql)
                break
            except Exception as e:          # noqa: BLE001 — 재시도 후 위로
                last = e
                log.warning("청크 %s 시도 %d 실패: %s", u.label(), attempt + 1, e)
        else:
            raise RuntimeError(f"청크 {u.label()} 추출 실패") from last
        df = normalize_schema(df)
        suffix = f"_g{u.group + 1}" if u.n_groups > 1 else ""
        p = staging / (f"raw_{u.chunk.d_from:%Y%m%d}_{u.chunk.d_to:%Y%m%d}_"
                       f"{uuid.uuid4().hex[:8]}{suffix}.parquet")
        df.write_parquet(p)
        return p

    done = 0
    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futs = {ex.submit(work, u): u for u in units}
        for fut in as_completed(futs):
            got = fut.result()              # 실패 시 여기서 전파
            done += 1
            if got is None:
                continue
            files.append(got)
            if on_progress:                 # 어느 그룹에서 시간이 가는지 보이게
                on_progress(done, total, futs[fut].label())
    return sorted(files)
