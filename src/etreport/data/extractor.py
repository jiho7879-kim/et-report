"""Impala 추출 — 청크 분할 → 병렬(가용 코어의 60%) → long parquet(명시 스키마).

bigdataquery(bdq)는 스레드 안전 확인됨. 청크는 (기간×lot) greedy binning으로
비슷한 크기가 되도록 나누고, 실패 청크는 자동 재시도한다.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pyarrow as pa

from etreport.config.catalog import Catalog
from etreport.config.settings import Condition
from etreport.data.querybuilder import (
    ITEM_ID_CHUNK,
    build_date_probe_sql,
    build_extract_sql,
)
from etreport.paths import STAGING_KEEP_DAYS

log = logging.getLogger(__name__)

#: 추출 병렬도 상한을 **가용 CPU의 몇 할로 잡을지**. 전부 쓰지 않는 이유는
#: 추출이 도는 동안에도 같은 PC에서 Excel COM과 화면이 돌아야 하기 때문이다.
WORKER_CPU_RATIO = 0.60
RETRY = 2


def available_cpus() -> int:
    """이 프로세스가 실제로 쓸 수 있는 코어 수. 못 읽으면 1로 본다.

    `os.cpu_count()`는 컨테이너·affinity 제한을 모른다 — 있으면 affinity를
    먼저 본다(리눅스/WSL). 사내 Windows PC에서는 둘이 같은 값이다.
    """
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def plan_cpu_workers(cpus: int | None = None,
                     ratio: float = WORKER_CPU_RATIO) -> int:
    """워커 상한 = 가용 코어 × 비율. 최소 1 — 1코어 PC에서도 추출은 돈다."""
    n = available_cpus() if cpus is None else max(1, int(cpus))
    return max(1, round(n * ratio))


#: 동시에 띄우는 조회 수의 **상한**. 실제 병렬도는 첫 청크를 실측한 뒤
#: `plan_workers()`가 메모리를 보고 이 값 이하로 다시 정한다.
N_WORKERS = plan_cpu_workers()

#: 한 실행 단위가 한 번에 들고 올 행 수의 예산. 넘으면 **다음 단위부터** item
#: 그룹을 더 잘게 쪼갠다(`plan_group_size`). 실측 규모(20만행/일 · item 1000)는
#: 여기에 한참 못 미치므로 평소에는 아무 일도 하지 않고, item 조건이 비정상적으로
#: 넓을 때만 한 조회가 메모리를 통째로 먹는 것을 막는다.
MAX_ROWS_PER_UNIT = 2_000_000

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


def plan_chunks(d_from: date, d_to: date, max_days: int = 1,
                days: list[date] | None = None) -> list[Chunk]:
    """기간을 `max_days`일씩 쪼갠다. lot 조건이 좁으면 max_days를 늘려도 된다.

    `days`를 주면 **그 날짜만** 청크로 만든다(날짜 프로브 §2). 연속한 날짜끼리만
    한 청크로 묶으므로, 비어 있는 날짜를 건너뛰면서도 청크 하나의 SQL은 여전히
    연속 구간 하나다. 빈 리스트는 "조회할 날짜가 없다"는 뜻이라 청크도 없다.
    """
    span = [d_from + timedelta(days=i) for i in range((d_to - d_from).days + 1)] \
        if days is None else sorted({d for d in days if d_from <= d <= d_to})
    width = max(1, int(max_days))
    out: list[Chunk] = []
    for d in span:
        last = out[-1] if out else None
        if last is not None and last.d_to + timedelta(days=1) == d \
                and (last.d_to - last.d_from).days + 1 < width:
            out[-1] = Chunk(last.d_from, d)
        else:
            out.append(Chunk(d, d))
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


def item_groups(item_ids: list[str] | None,
                group_size: int = ITEM_ID_CHUNK) -> list[list[str] | None]:
    """item 목록을 상한(9999) 이하 그룹으로 나눈다. 필터가 없으면 그룹 하나.

    `group_size`는 Impala `IN` 상한보다 **작게** 줄일 때만 쓴다(행 수 기준 재분할).
    """
    if not item_ids:
        return [None]
    size = max(1, min(int(group_size), ITEM_ID_CHUNK))
    return [item_ids[s:s + size] for s in range(0, len(item_ids), size)]


def plan_units(d_from: date, d_to: date,
               item_ids: list[str] | None = None,
               max_days: int = 1,
               group_size: int = ITEM_ID_CHUNK,
               days: list[date] | None = None) -> list[Unit]:
    """기간 × item 그룹의 **곱**을 실행 단위 목록으로 편다."""
    groups = item_groups(item_ids, group_size)
    return [Unit(ch, i, len(groups), ids)
            for ch in plan_chunks(d_from, d_to, max_days, days)
            for i, ids in enumerate(groups)]


def plan_group_size(rows: int | None, n_items: int | None,
                    max_rows: int | None = None) -> int | None:
    """실측 행 수가 예산을 넘으면 **그 다음부터 쓸** item 그룹 크기.

    줄일 수 있는 축은 item뿐이다 — 기간은 1일이 이미 최소이고, 조회 조건은
    사용자의 것이라 앱이 좁힐 수 없다. 넘지 않았거나 실측이 없으면 `None`
    (지금 크기 유지). 행 수는 프레임 크기의 대리 지표이므로 비례해서 줄인다.
    """
    budget = MAX_ROWS_PER_UNIT if max_rows is None else max_rows
    if not rows or not n_items or rows <= budget:
        return None
    return max(1, int(n_items * budget // rows))


def resplit_units(units: list[Unit], group_size: int) -> list[Unit]:
    """아직 시작하지 않은 단위의 item 그룹을 `group_size` 이하로 다시 쪼갠다.

    같은 기간 청크의 item을 다시 모아 나누므로 **item이 하나도 빠지지 않는다.**
    item 필터가 없는 청크(전체 조회)는 쪼갤 축이 없어 그대로 둔다.
    """
    by_chunk: dict[Chunk, list[str] | None] = {}
    for u in units:                      # 기간 순서를 유지한다(dict는 삽입 순)
        if u.item_ids is None:
            by_chunk.setdefault(u.chunk, None)
        else:
            cur = by_chunk.get(u.chunk)
            by_chunk[u.chunk] = u.item_ids[:] if cur is None else cur + u.item_ids
    out: list[Unit] = []
    for ch, items in by_chunk.items():
        groups = item_groups(items, group_size)
        out += [Unit(ch, i, len(groups), ids) for i, ids in enumerate(groups)]
    return out


def available_memory_bytes() -> int | None:
    """호스트가 즉시 쓸 수 있는 메모리 바이트 수(알 수 없으면 ``None``).

    외부 의존성을 늘리지 않고 Linux/Windows 모두에서 쓸 수 있는 정보만 쓴다.
    이 값은 성능 힌트일 뿐, 읽지 못했다고 추출을 막지는 않는다.
    """
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages) * int(page_size)
    except (AttributeError, OSError, ValueError):
        return None


def plan_workers(available_bytes: int | None, estimated_bytes: int | None,
                 cap: int = N_WORKERS) -> int:
    """pandas+Arrow/Polars의 동시 상주량을 감안한 안전한 워커 수.

    한 조회는 변환 중 원본과 변환본이 함께 살아 최대 약 두 배를 쓴다. 추정치가
    없거나 메모리 정보를 못 읽으면 기존의 상한을 유지한다. 항상 하나 이상을
    돌려, 보수적인 추정이 추출 자체를 멈추게 하지는 않는다.
    """
    cap = max(1, cap)
    if not available_bytes or not estimated_bytes or estimated_bytes <= 0:
        return cap
    # 가용 메모리의 60%만 추출에 쓰고, 변환 중 두 벌을 고려한다.
    return max(1, min(cap, int(available_bytes * 0.60 // (estimated_bytes * 2))))


def _fetch(sql: str) -> pl.DataFrame:
    import bigdataquery as bdq  # 사내 패키지

    pdf = bdq.getData(sql)       # pandas 반환 가정
    try:
        # pandas → Arrow → Polars는 pandas 버퍼를 되복사하는 경로보다 피크를
        # 낮춘다. Arrow 변환을 못 하는 사내 pandas 타입은 기존 경로로 폴백한다.
        try:
            return pl.from_arrow(pa.Table.from_pandas(pdf, preserve_index=False))
        except (pa.ArrowException, TypeError, ValueError):
            return pl.from_pandas(pdf)
    finally:
        # 반환 직후 원본 pandas 프레임을 계속 붙들면 워커마다 두 프레임이 남는다.
        del pdf


#: 날짜 프로브를 켜는 조건 컬럼. 이 컬럼이 조건에 있으면 lot이 좁다는 뜻이라,
#: 기간 대부분의 날짜에는 그 lot의 측정이 아예 없다(§2).
LOT_COL = "root_lot_id"


def has_lot_filter(conditions: list[Condition]) -> bool:
    return any(c.col == LOT_COL and c.val.strip() for c in conditions)


def _days_of(df: pl.DataFrame) -> list[date]:
    """프로브 결과 한 컬럼을 date 목록으로. 문자열·시각·날짜 어느 쪽이든 받는다."""
    s = df.to_series(0)
    if s.dtype in (pl.Categorical, pl.Enum):
        s = s.cast(pl.Utf8)
    if s.dtype == pl.Utf8:
        s = s.str.to_datetime(time_unit="us", strict=False)
    if s.dtype == pl.Datetime:
        s = s.dt.date()
    elif s.dtype != pl.Date:
        s = s.cast(pl.Date, strict=False)
    return sorted(set(s.drop_nulls().to_list()))


def probe_days(conditions: list[Condition], d_from: date, d_to: date,
               catalog: Catalog) -> list[date] | None:
    """lot 조건이 있으면 **데이터가 있는 날짜만** 먼저 알아낸다(§2).

    반환이 `None`이면 예전 그대로 기간 전체를 돈다 — lot 조건이 없거나 프로브가
    실패했을 때다. 프로브 실패로 추출 자체를 막지는 않는다(가벼운 최적화이지
    정확성의 전제가 아니다). 빈 리스트는 "그 기간에 데이터가 없다"는 뜻이다.
    """
    if not has_lot_filter(conditions):
        return None
    try:
        sql = build_date_probe_sql(conditions, d_from, d_to, catalog)
        df = _fetch(sql)
    except Exception as e:                  # noqa: BLE001 — 최적화 실패는 치명적이 아니다
        log.warning("날짜 프로브 실패(%s) — 기간 전체를 조회합니다", e)
        return None
    if df.is_empty() or not df.width:
        return []
    try:
        return _days_of(df)
    except Exception as e:                  # noqa: BLE001
        log.warning("날짜 프로브 결과를 읽지 못했습니다(%s)", e)
        return None


def _is_memory_error(error: BaseException) -> bool:
    """재시도하면 악화되는 메모리 부족 오류인지 판정한다."""
    return isinstance(error, (MemoryError, pa.ArrowMemoryError))


def _target_dtypes() -> dict[str, pl.DataType]:
    return {f.name: pl.from_arrow(pa.array([], f.type)).dtype for f in ARROW_SCHEMA}


def canonicalize_schema_columns(df: pl.DataFrame,
                                target: dict[str, pl.DataType]) -> pl.DataFrame:
    """`getData`가 바꾼 키 컬럼 표기를 고정 스키마 이름으로 되돌린다.

    Impala/bigdataquery 조합에 따라 같은 ``SELECT step_seq``도 결과 DataFrame에는
    ``STEP_SEQ`` 또는 공백이 붙은 `` step_seq ``로 돌아올 수 있다. 예전 코드는
    대소문자를 구분해 찾았으므로, 실제 값이 있어도 ``step_seq``가 "없는 컬럼"으로
    취급되어 NULL 열을 새로 만들었다. 그 NULL은 key_hash와 retest 판정에서 서로
    다른 seq를 한 행으로 보게 해 item 값을 잃게 만든다.

    고정 스키마의 이름만 대소문자·앞뒤 공백 무시로 맞춘다. 같은 이름의 열이 둘
    이상이면 첫 non-NULL 값을 합쳐 하나로 만든다. 원본 값을 버리기보다 합치는
    편이 getData 드라이버가 중복 레이블을 반환한 경우에도 안전하다.
    """
    matched: dict[str, list[str]] = {}
    for col in df.columns:
        canonical = col.strip().casefold()
        if canonical in target:
            matched.setdefault(canonical, []).append(col)

    # 중복 표기(`step_seq`와 `STEP_SEQ`)는 먼저 coalesce한다. rename부터 하면
    # Polars가 같은 이름의 두 열을 허용하지 않아 값을 잃거나 예외가 난다.
    for canonical, cols in matched.items():
        if len(cols) < 2:
            continue
        ordered = ([canonical] if canonical in cols else []) + [
            c for c in cols if c != canonical]
        log.warning("조회 결과의 %s 열 %d개를 non-NULL 우선으로 합칩니다: %s",
                    canonical, len(cols), ", ".join(cols))
        df = df.with_columns(
            pl.coalesce([pl.col(c) for c in ordered]).alias(canonical))
        df = df.drop([c for c in cols if c != canonical])

    # 중복을 정리한 뒤 단일 비표준 표기만 안전하게 rename한다.
    rename: dict[str, str] = {}
    for canonical in target:
        cols = [c for c in df.columns if c.strip().casefold() == canonical]
        if len(cols) == 1 and cols[0] != canonical:
            rename[cols[0]] = canonical
    return df.rename(rename) if rename else df


def _coerce_numeric_strings(df: pl.DataFrame,
                            target: dict[str, pl.DataType]) -> pl.DataFrame:
    """문자열 수치 키를 안전하게 숫자로 읽는다.

    일부 getData 결과는 INT/DECIMAL 열도 pandas object 문자열로 돌려주며,
    특히 ``step_seq``가 ``"1.0"`` 형태가 된다. Polars의 String→Int32 직접
    cast는 그 값을 NULL로 바꾸므로 Float64를 한 번 거쳐 고정 타입으로 내린다.
    빈 문자열·숫자가 아닌 값은 기존과 같이 NULL이다.
    """
    numeric = {pl.Int32, pl.Float64}
    exprs = [
        pl.col(col).str.strip_chars().cast(pl.Float64, strict=False)
        .cast(dtype, strict=False).alias(col)
        for col, dtype in target.items()
        if col in df.columns and dtype in numeric and df.schema[col] == pl.Utf8
    ]
    return df.with_columns(exprs) if exprs else df


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
    # getData가 SQL의 소문자 레이블을 대문자로 돌려도 값이 NULL 열로 바뀌지
    # 않게, 타입 변환보다 먼저 키 이름을 표준화한다.
    df = canonicalize_schema_columns(df, target)

    cat = [c for c, t in zip(df.columns, df.dtypes)
           if t in (pl.Categorical, pl.Enum)]
    if cat:
        df = df.with_columns([pl.col(c).cast(pl.Utf8) for c in cat])

    parse = [pl.col(c).str.to_datetime(time_unit="us", strict=False).alias(c)
             for c, dt in target.items()
             if c in df.columns and dt == pl.Datetime and df.schema[c] == pl.Utf8]
    if parse:
        df = df.with_columns(parse)

    df = _coerce_numeric_strings(df, target)
    df = df.cast({c: dt for c, dt in target.items() if c in df.columns},
                 strict=False)

    missing = [c for c in target if c not in df.columns]
    if missing:
        log.warning("조회 결과에 없는 컬럼을 null로 채웁니다: %s", ", ".join(missing))
        df = df.with_columns([pl.lit(None, dtype=target[c]).alias(c)
                              for c in missing])
    df = correct_temperature(df)
    rest = [c for c in df.columns if c not in target]
    return df.select([*target, *rest])


def correct_temperature(df: pl.DataFrame,
                        col: str = "temperature") -> pl.DataFrame:
    """측정 온도를 **가장 가까운 5의 배수**로 보정한다 — 23.9→25, 149→150.

    계측기가 준 raw 값(23.9·24.9…)을 그대로 두면 같은 조건의 측정이 온도별로
    갈라진다. 보정은 **여기, 추출 직후**에 한다 — 그래야 리포메팅도 적재도
    보정된 값으로 진행되고 **DuckDB에도 보정된 값이 저장된다**(요청 §10).
    읽는 시점(`data/compat.temp_expr`)에도 같은 보정이 걸려 있는데, 반올림은
    멱등이라 이미 보정된 값에 다시 걸어도 결과가 같다 — 예전에 raw로 적재해
    둔 DB도 화면·표·PPT가 맞는다.

    단위는 `data/compat.TEMP_STEP` 하나로 맞춘다(규칙을 두 곳에 두지 않는다).
    반올림은 **DuckDB `ROUND`와 같은 규칙**(0.5는 0에서 먼 쪽)으로 맞춘다 —
    polars의 `round`는 짝수로 붙는(banker's) 방식이라 22.5°C 같은 경계값에서
    적재값과 조회값이 갈린다. `tests/test_extract_schema.py`가 둘이 같은지
    확인한다.
    """
    from etreport.data.compat import TEMP_STEP
    if col not in df.columns:
        return df
    q = pl.col(col).cast(pl.Float64, strict=False) / TEMP_STEP
    half_away = (q.abs() + 0.5).floor() * q.sign()
    return df.with_columns((half_away * TEMP_STEP).alias(col))


def chunk_path(staging: Path, u: Unit, sql: str) -> Path:
    """청크 parquet 경로. 이름이 **SQL로 결정된다**(§11 재사용의 열쇠).

    예전에는 uuid를 붙여 매번 새 이름이 나왔다 — 그래서 조건이 똑같아도 이미 받아
    둔 파일을 찾을 길이 없었다. SQL에는 조건·기간·item 목록이 전부 들어 있으므로
    그 해시가 곧 "같은 조회"의 정의다. 조건이 한 글자라도 다르면 다른 파일이 된다.
    """
    h = hashlib.blake2b(sql.encode("utf-8"), digest_size=6).hexdigest()
    suffix = f"_g{u.group + 1}" if u.n_groups > 1 else ""
    return staging / (f"raw_{u.chunk.d_from:%Y%m%d}_{u.chunk.d_to:%Y%m%d}_"
                      f"{h}{suffix}.parquet")


def is_fresh(path: Path, keep_days: int = STAGING_KEEP_DAYS) -> bool:
    """보관 기간 안에 받아 둔 파일인가. cleanup_staging과 같은 기준을 쓴다."""
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age < keep_days * 86400


def extract_to_parquet(
    conditions: list[Condition],
    d_from: date,
    d_to: date,
    catalog: Catalog,
    staging: Path,
    on_progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    item_ids: list[str] | None = None,
    reuse: bool = False,
    max_days: int = 1,
    days: list[date] | None = None,
) -> list[Path]:
    """청크별 parquet 파일 목록 반환. 파일명에 기간·item 그룹·SQL 해시 포함.

    `reuse=True`면 **같은 SQL로 이미 받아 둔 parquet이 staging에 남아 있으면
    다시 조회하지 않는다**(§11). 리포메터만 고쳐 다시 돌릴 때 수십 분짜리
    재추출을 건너뛰기 위한 것이라 기본은 꺼짐이다 — 조건이 같아도 원본 테이블이
    바뀌었으면 옛 값을 쓰게 되므로, 켜는 것은 사용자가 정한다.

    **청크 = 기간 × item 그룹의 곱**(확정 사양 §4.2)이고, 그 곱 전체가 병렬
    대상이다 — item이 3그룹이면 7일치는 7개가 아니라 21개를 4워커가 나눠 문다.
    bdq는 스레드 안전이 확인됐다.

    downstream(pivot_and_load)은 parquet 전체를 scan_parquet로 합치고 key_hash로
    dedup하므로 파일이 여러 개로 갈라져도 안전하다.
    """
    units = plan_units(d_from, d_to, item_ids, max_days=max_days, days=days)
    files: list[Path] = []
    total = len(units)

    abort = threading.Event()

    def stopped() -> bool:
        return abort.is_set() or (should_stop is not None and should_stop())

    def work(u: Unit) -> tuple[Path, int, int] | None:
        if stopped():
            return None
        sql = build_extract_sql(conditions, u.chunk.d_from, u.chunk.d_to,
                               catalog, item_ids=u.item_ids)
        p = chunk_path(staging, u, sql)
        if reuse and is_fresh(p):
            rows = pl.scan_parquet(str(p)).select(pl.len()).collect().item()
            log.info("청크 %s 재사용 — %s (%s행)", u.label(), p.name, f"{rows:,}")
            return p, p.stat().st_size, rows
        last: Exception | None = None
        for attempt in range(RETRY + 1):
            try:
                if stopped():
                    return None
                df = _fetch(sql)
                break
            except Exception as e:
                if _is_memory_error(e):
                    abort.set()
                    raise RuntimeError(
                        "메모리가 부족해 추출을 중단했습니다. 기간 또는 item 조건을 "
                        "줄인 뒤 다시 시도하세요."
                    ) from e
                last = e
                log.warning("청크 %s 시도 %d 실패: %s", u.label(), attempt + 1, e)
        else:
            raise RuntimeError(f"청크 {u.label()} 추출 실패") from last
        df = normalize_schema(df)
        # 반쯤 쓰다 죽은 파일이 다음 실행에서 '받아 둔 것'으로 재사용되면 안 된다.
        tmp = p.with_suffix(".tmp")
        df.write_parquet(tmp)
        os.replace(tmp, p)
        return p, df.estimated_size(), df.height

    done = 0
    # 첫 청크는 **단독으로** 실행해 실측 행 수와 프레임 크기를 얻는다. 그 값으로
    # ① 동시 상주량에 맞춘 병렬도와 ② 남은 단위의 item 그룹 크기를 정한다.
    # 전 단위를 한꺼번에 submit하지 않으므로 첫 OOM 뒤에 큐의 청크가 계속
    # 시작되는 일도 막힌다. 풀 자체는 상한(N_WORKERS)으로 만들고 **동시에 띄우는
    # 수**를 `workers`로 조절한다 — 풀을 1로 만들면 나중에 늘릴 수 없다.
    workers = 1
    measured = False
    pending = iter(units)
    with ThreadPoolExecutor(max_workers=max(1, N_WORKERS)) as ex:
        futs: dict = {}
        for _ in range(workers):
            try:
                u = next(pending)
            except StopIteration:
                break
            futs[ex.submit(work, u)] = u
        while futs:
            fut = next(as_completed(futs))
            u = futs.pop(fut)
            try:
                got = fut.result()          # 실패 시 여기서 전파
            except BaseException:
                abort.set()
                for queued in futs:
                    queued.cancel()
                raise
            done += 1
            if got is None:
                if stopped():
                    continue
            else:
                path, estimated_bytes, rows = got
                files.append(path)
                if on_progress:             # 어느 그룹에서 시간이 가는지 보이게
                    on_progress(done, total, u.label())
                if not measured:
                    measured = True
                    workers = min(max(1, total - done), plan_workers(
                        available_memory_bytes(), estimated_bytes))
                    size = plan_group_size(rows, len(u.item_ids or []))
                    if size is not None:
                        rest = resplit_units(list(pending), size)
                        total = done + len(futs) + len(rest)
                        pending = iter(rest)
                        log.warning(
                            "청크 %s가 %s행 — item 그룹을 %d개씩으로 줄여 "
                            "남은 %d단위로 다시 나눕니다",
                            u.label(), f"{rows:,}", size, len(rest))
            if not stopped():
                while len(futs) < workers:
                    try:
                        next_unit = next(pending)
                    except StopIteration:
                        break
                    futs[ex.submit(work, next_unit)] = next_unit
    return sorted(files)
