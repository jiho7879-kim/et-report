"""DuckDB 저장소.

et_data(wide)   : 키 9종 + key_hash + item 컬럼들. INSERT BY NAME으로 스키마 진화.
                  테이블 이름은 손코딩 시절과 동일 — `select * from et_data` 그대로.
load_log        : 파일 단위 적재 이력(1단 중복 차단)
뷰             : et_data → v_latest(retest 최신)

제외 포인트는 이 DB에 쓰지 않는다. 분석 화면이 DB를 **읽기 전용**으로 열기
때문에, 제외는 data/exclusions.py 의 사이드카 JSON에 DB 경로별로 저장한다.
"""
from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl

log = logging.getLogger(__name__)

KEY9 = ["root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos",
        "temperature", "step_id", "step_seq", "total_site_cnt", "tkout_time"]
KEY8 = KEY9[:-1]                     # retest 구분 제외한 논리 키
N_BUCKETS = 512                      # 피벗 메모리 다이얼 상한 (계획서 §4)
TARGET_CELLS = 4_000_000             # 버킷 하나가 만드는 wide 셀 수 목표(≈32MB)
TABLE = "et_data"                    # 손코딩 시절과 동일한 이름
LEGACY_TABLES = ("fact",)            # 예전 버전이 만든 DB도 읽는다


def plan_buckets(n_keys: int, n_items: int) -> int:
    """피벗 한 번이 만드는 셀(키×item)이 TARGET_CELLS를 넘지 않는 최소 버킷 수.

    버킷은 **피벗 메모리를 나누기 위한 작업 단위일 뿐** 저장되는 내용과는
    무관하다 — key_hash도, 컬럼도, 중복 제거 결과도 버킷 수와 무관하게 같다.
    그래서 예전에 512개로 적재해 둔 DB에 이어 적재해도 안전하다.

    512개 고정이던 시절에는 키가 적고 item이 많은(=현장에서 가장 흔한) 모양에서
    거의 빈 버킷 수백 개에 피벗과 INSERT를 반복했다. 20만 행 적재의 대부분이
    그 오버헤드였다.
    """
    if n_keys <= 0 or n_items <= 0:
        return 1
    return max(1, min(N_BUCKETS, math.ceil(n_keys * n_items / TARGET_CELLS)))


def _connect_write(path: Path) -> duckdb.DuckDBPyConnection:
    """쓰기 연결. 읽기 전용 연결이 남아 있으면 한 번 정리하고 다시 시도한다.

    DuckDB는 **같은 파일에 설정이 다른 연결을 함께 열지 못한다** — 분석 화면이
    그 DB를 읽기 전용으로 붙잡고 있으면 적재가
    `can't open a connection to same database file with a different
    configuration than existing connections`로 떨어진다. 기존 DB에 이어
    적재할 때(특히 신규 item_id를 추가한 같은 lot을 다시 적재할 때) 자주
    난다. UI가 `loader.close_store()`로 먼저 닫아 주지만, 이미 참조가 끊겼는데
    아직 수거되지 않은 연결이 남아 있을 수 있어 여기서 한 번 더 밀어 준다.
    """
    import gc

    try:
        return duckdb.connect(str(path))
    except duckdb.Error as first:
        if "different configuration" not in str(first) \
                and "already open" not in str(first):
            raise
        gc.collect()                     # 참조가 끊긴 읽기 전용 연결을 수거
        try:
            return duckdb.connect(str(path))
        except duckdb.Error as e:
            from etreport.data.loader import explain_conn_error
            raise RuntimeError(
                explain_conn_error(e, str(path), write=True)) from e


def _limit_memory(con: duckdb.DuckDBPyConnection) -> None:
    """쓰기 연결에도 읽기 연결과 같은 메모리 천장과 디스크 스필을 건다.

    설정하지 않으면 DuckDB가 물리 메모리의 80%까지 쓴다 — 적재는 추출이 남긴
    polars 프레임과 함께 도는데, 그 위에서 피벗·INSERT가 캐시를 다 채우면
    프로세스가 OOM으로 떨어진다. 상한을 넘는 몫은 임시 폴더로 흘려보낸다.
    """
    from etreport.data.loader import readonly_config
    cfg = readonly_config()
    try:
        con.execute(f"SET memory_limit='{cfg['memory_limit']}'")
        if tmp := cfg.get("temp_directory"):
            con.execute(f"SET temp_directory='{tmp.replace(chr(39), chr(39) * 2)}'")
    except duckdb.Error as e:                     # 설정 실패로 적재를 막지 않는다
        log.warning("DuckDB 메모리 설정 실패(기본값으로 진행): %s", e)


def key_hash_expr() -> pl.Expr:
    """포인트 식별자. **절대 바꾸지 말 것** — 기존 DB에 이어 적재할 때 이 값으로
    중복을 걸러내므로, 계산식이 바뀌면 같은 포인트가 두 번 들어간다.
    """
    joined = pl.concat_str([pl.col(c).cast(pl.Utf8).fill_null("␀") for c in KEY9],
                           separator="|")
    return joined.map_elements(
        lambda s: hashlib.blake2b(s.encode(), digest_size=8).hexdigest(),
        return_dtype=pl.Utf8,
    )


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.con = _connect_write(self.path)
        self.con.execute("PRAGMA threads=4")
        _limit_memory(self.con)
        self._ensure_meta()

    # ── 스키마 ────────────────────────────────────────────────
    def _ensure_meta(self) -> None:
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS load_log(
                file_name VARCHAR PRIMARY KEY,
                loaded_at TIMESTAMP, rows BIGINT, note VARCHAR)""")

    def close(self) -> None:
        """쓰기 연결을 닫는다 — DuckDB는 파일을 배타적으로 잠그므로, 적재 후
        닫지 않으면 곧바로 이어지는 읽기 전용 열기가 실패한다."""
        try:
            self.con.close()
        except Exception as e:                    # noqa: BLE001 — 이미 닫혔을 수 있다
            log.debug("Store 닫기 실패(무시): %s", e)

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def table_name(self) -> str | None:
        """존재하는 데이터 테이블 이름 (et_data 우선, 없으면 예전 이름)."""
        for t in (TABLE, *LEGACY_TABLES):
            if self.con.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name=?",
                    [t]).fetchone():
                return t
        return None

    # ── 적재 ──────────────────────────────────────────────────
    def load_wide(self, wide: pl.DataFrame, src_file: str) -> int:
        """버킷 하나 분량의 wide를 dedup 후 적재. 신규 item 컬럼은 자동 추가.

        반환값은 **실제로 들어간 행 수**다(중복으로 걸러진 것은 빼고). 화면의
        '적재 완료 — N행'이 이 값이므로, 같은 파일을 다시 적재하면 0이 나온다.
        """
        if wide.is_empty():
            return 0
        self.con.register("incoming", wide.to_arrow())
        tbl = self.table_name()
        if tbl is None:
            self.con.execute(
                'CREATE TABLE "' + TABLE + '" AS SELECT * FROM incoming')
            inserted = len(wide)
        else:
            have = {r[0] for r in self.con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=?", [tbl]).fetchall()}
            for col, dt in zip(wide.columns, wide.dtypes):
                if col not in have:
                    dd = "DOUBLE" if dt in (pl.Float64, pl.Float32) else "VARCHAR"
                    self.con.execute(
                        f'ALTER TABLE "{tbl}" ADD COLUMN "{col}" {dd}')
            # 2단: 행 단위 중복 차단. DuckDB는 INSERT 결과로 넣은 행 수를 준다.
            got = self.con.execute(
                f'INSERT INTO "{tbl}" BY NAME SELECT i.* FROM incoming i '
                f'ANTI JOIN "{tbl}" f USING(key_hash)').fetchone()
            inserted = int(got[0]) if got else 0
        self.con.unregister("incoming")
        self.con.execute(
            "INSERT OR REPLACE INTO load_log VALUES (?, ?, ?, '')",
            [src_file, datetime.now(), inserted])
        return inserted

    # ── 뷰 체인 ───────────────────────────────────────────────
    def rebuild_views(self) -> None:
        """retest 최신만 남기는 뷰. 제외는 여기서 다루지 않는다(사이드카)."""
        tbl = self.table_name()
        if tbl is None:
            return
        k8 = ", ".join(KEY8)
        self.con.execute(
            f'CREATE OR REPLACE VIEW v_latest AS SELECT * FROM "{tbl}" '
            f'QUALIFY row_number() OVER (PARTITION BY {k8} '
            f'ORDER BY tkout_time DESC) = 1')

    # ── 조회 도우미 ───────────────────────────────────────────
    def lots(self) -> list[str]:
        tbl = self.table_name()
        if tbl is None:
            return []
        return [r[0] for r in self.con.execute(
            f'SELECT DISTINCT root_lot_id FROM "{tbl}" ORDER BY 1').fetchall()]

    def valid_wafers(self, lot: str) -> list[str]:
        """그룹 편집 모달의 '조회' 버튼 — 측정 있는 wafer만."""
        tbl = self.table_name()
        if tbl is None:
            return []
        return [r[0] for r in self.con.execute(
            f'SELECT DISTINCT wafer_id FROM "{tbl}" WHERE root_lot_id=? '
            f'ORDER BY 1', [lot]).fetchall()]


def pivot_and_load(store: Store, parquet_files: list[Path],
                   on_progress=None) -> int:
    """long parquet들 → key_hash 버킷 재파티션 → 버킷별 피벗 → 적재.

    item은 step 간 거의 중복(밀집)이므로 wide가 정답 구조다 — 계획서 §4 참조.
    피벗 메모리 피크는 버킷 수로 제어하되, 그 수는 데이터 모양(키 수 × item 수)을
    보고 정한다(plan_buckets). 버킷은 작업 단위일 뿐이라 **DB에 들어가는 내용은
    버킷 수와 무관**하다 — 예전 DB에 이어 적재해도 결과가 같다.

    성능 주의 두 가지:
      1) key_hash는 행마다 blake2b를 부르는 파이썬 UDF라 비싸다. 예전에는
         버킷마다 lazy를 collect해서 512회 전체 재계산이 일어났다(20만 행 195초).
         아래처럼 한 번만 실체화한다.
      2) 버킷 하나마다 피벗 1회 + DuckDB INSERT 1회가 붙는다. 512개 고정이면
         키 200개짜리 하루치에도 그 왕복이 170번 생겼다.
    """
    total = 0
    lazy = pl.scan_parquet([str(p) for p in parquet_files])
    cols = lazy.collect_schema().names()
    if "et_value" in cols and "value" not in cols:
        lazy = lazy.rename({"et_value": "value"})
    frame = lazy.with_columns(key_hash=key_hash_expr()).collect()
    if frame.is_empty():
        return 0

    n_keys = frame["key_hash"].n_unique()
    n_items = frame["item_id"].n_unique()
    n_buckets = plan_buckets(n_keys, n_items)
    log.info("적재 버킷 %d개 — 키 %d · item %d · %d행",
             n_buckets, n_keys, n_items, frame.height)

    if n_buckets == 1:
        parts = {0: frame}
    else:
        frame = frame.with_columns(
            bucket=pl.col("key_hash").str.slice(0, 4)
                   .str.to_integer(base=16) % n_buckets)
        parts = {(k[0] if isinstance(k, tuple) else k): v.drop("bucket")
                 for k, v in frame.partition_by("bucket", as_dict=True).items()}

    for b in range(n_buckets):
        part = parts.get(b)
        if part is not None and not part.is_empty():
            wide = part.pivot(on="item_id", index=[*KEY9, "line_id", "key_hash"],
                              values="value", aggregate_function="first")
            total += store.load_wide(wide, src_file=f"bucket_{b}")
        if on_progress:
            on_progress(b + 1, n_buckets)
    for p in parquet_files:
        store.con.execute(
            "INSERT OR REPLACE INTO load_log VALUES (?, ?, 0, 'file')",
            [p.name, datetime.now()])
    store.rebuild_views()
    return total
