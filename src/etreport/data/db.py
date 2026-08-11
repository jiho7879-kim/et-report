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
from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl

log = logging.getLogger(__name__)

KEY9 = ["root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos",
        "temperature", "step_id", "step_seq", "total_site_cnt", "tkout_time"]
KEY8 = KEY9[:-1]                     # retest 구분 제외한 논리 키
N_BUCKETS = 512                      # 피벗 메모리 다이얼 (계획서 §4)
TABLE = "et_data"                    # 손코딩 시절과 동일한 이름
LEGACY_TABLES = ("fact",)            # 예전 버전이 만든 DB도 읽는다


def key_hash_expr() -> pl.Expr:
    joined = pl.concat_str([pl.col(c).cast(pl.Utf8).fill_null("␀") for c in KEY9],
                           separator="|")
    return joined.map_elements(
        lambda s: hashlib.blake2b(s.encode(), digest_size=8).hexdigest(),
        return_dtype=pl.Utf8,
    )


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.con = duckdb.connect(str(self.path))
        self.con.execute("PRAGMA threads=4")
        self._ensure_meta()

    # ── 스키마 ────────────────────────────────────────────────
    def _ensure_meta(self) -> None:
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS load_log(
                file_name VARCHAR PRIMARY KEY,
                loaded_at TIMESTAMP, rows BIGINT, note VARCHAR)""")

    def table_name(self) -> str | None:
        """존재하는 데이터 테이블 이름 (et_data 우선, 없으면 예전 이름)."""
        for t in (TABLE, *LEGACY_TABLES):
            if self.con.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name=?",
                    [t]).fetchone():
                return t
        return None

    def fact_exists(self) -> bool:
        return self.table_name() is not None

    # ── 적재 ──────────────────────────────────────────────────
    def already_loaded(self, file_name: str) -> bool:
        return bool(self.con.execute(
            "SELECT 1 FROM load_log WHERE file_name=?", [file_name]).fetchone())

    def load_wide(self, wide: pl.DataFrame, src_file: str) -> int:
        """버킷 하나 분량의 wide를 dedup 후 적재. 신규 item 컬럼은 자동 추가."""
        if wide.is_empty():
            return 0
        self.con.register("incoming", wide.to_arrow())
        tbl = self.table_name()
        if tbl is None:
            self.con.execute(
                'CREATE TABLE "' + TABLE + '" AS SELECT * FROM incoming')
        else:
            have = {r[0] for r in self.con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=?", [tbl]).fetchall()}
            for col, dt in zip(wide.columns, wide.dtypes):
                if col not in have:
                    dd = "DOUBLE" if dt in (pl.Float64, pl.Float32) else "VARCHAR"
                    self.con.execute(
                        f'ALTER TABLE "{tbl}" ADD COLUMN "{col}" {dd}')
            # 2단: 행 단위 중복 차단
            self.con.execute(
                f'INSERT INTO "{tbl}" BY NAME SELECT i.* FROM incoming i '
                f'ANTI JOIN "{tbl}" f USING(key_hash)')
        self.con.unregister("incoming")
        self.con.execute(
            "INSERT OR REPLACE INTO load_log VALUES (?, ?, ?, '')",
            [src_file, datetime.now(), len(wide)])
        return len(wide)

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

    피벗 메모리 피크는 버킷 수(N_BUCKETS)로 제어한다. item은 step 간 거의
    중복(밀집)이므로 wide가 정답 구조다 — 계획서 §4 참조.

    성능 주의: key_hash는 행마다 blake2b를 부르는 파이썬 UDF라 비싸다.
    예전에는 버킷마다 lazy를 collect해서 **N_BUCKETS번(512회) 전체 재계산**이
    일어났다 — 20만 행 적재에 195초. 아래처럼 한 번만 실체화한 뒤 버킷으로
    쪼개면 같은 데이터가 0.6초에 들어간다. 메모리 피크는 리포메팅 단계에서
    이미 파일 하나를 통째로 읽는 것과 같은 수준이다.
    """
    total = 0
    lazy = pl.scan_parquet([str(p) for p in parquet_files])
    cols = lazy.collect_schema().names()
    if "et_value" in cols and "value" not in cols:
        lazy = lazy.rename({"et_value": "value"})
    lazy = lazy.with_columns(key_hash=key_hash_expr())
    lazy = lazy.with_columns(
        bucket=pl.col("key_hash").str.slice(0, 4)
               .str.to_integer(base=16) % N_BUCKETS)
    frame = lazy.collect()                       # ← key_hash 계산은 여기 한 번뿐
    parts = {(k[0] if isinstance(k, tuple) else k): v
             for k, v in frame.partition_by("bucket", as_dict=True).items()}
    for b in range(N_BUCKETS):
        part = parts.get(b)
        if part is None or part.is_empty():
            continue
        part = part.drop("bucket")
        wide = part.pivot(on="item_id", index=[*KEY9, "line_id", "key_hash"],
                          values="value", aggregate_function="first")
        total += store.load_wide(wide, src_file=f"bucket_{b}")
        if on_progress:
            on_progress(b + 1, N_BUCKETS)
    for p in parquet_files:
        store.con.execute(
            "INSERT OR REPLACE INTO load_log VALUES (?, ?, 0, 'file')",
            [p.name, datetime.now()])
    store.rebuild_views()
    return total
