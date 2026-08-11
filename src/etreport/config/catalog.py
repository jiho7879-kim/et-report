"""컬럼 카탈로그 — 앱 최초 실행 시 bdq.getColumnInfo('eds.f_et_test') 결과를
로컬 JSON에 캐시하고, 조건 빌더가 타입 정보를 여기서 얻는다."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from etreport.paths import catalog_cache_file, write_json_atomic

log = logging.getLogger(__name__)

TABLE = "eds.f_et_test"
NUMERIC = {"INT", "BIGINT", "SMALLINT", "TINYINT", "FLOAT", "DOUBLE", "DECIMAL"}


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    dtype: str      # Impala 타입 대문자

    @property
    def is_numeric(self) -> bool:
        return self.dtype.split("(")[0] in NUMERIC

    @property
    def is_timestamp(self) -> bool:
        return self.dtype.startswith("TIMESTAMP")


class Catalog:
    def __init__(self) -> None:
        self.columns: list[ColumnInfo] = []
        self.fetched_at: str = ""

    # ── 캐시 ──────────────────────────────────────────────────
    def load_cache(self) -> bool:
        f = catalog_cache_file()
        if not f.exists():
            return False
        raw = json.loads(f.read_text(encoding="utf-8"))
        self.columns = [ColumnInfo(c["name"], c["dtype"]) for c in raw["columns"]]
        self.fetched_at = raw.get("fetched_at", "")
        return True

    def save_cache(self) -> None:
        write_json_atomic(
            catalog_cache_file(),
            {
                "fetched_at": self.fetched_at,
                "columns": [{"name": c.name, "dtype": c.dtype}
                            for c in self.columns],
            },
            indent=1,
        )

    # ── 조회 ──────────────────────────────────────────────────
    def refresh(self) -> None:
        """bdq.getColumnInfo 실행 후 캐시 갱신. bdq 미설치 환경에서는 예외."""
        import bigdataquery as bdq  # 사내 패키지 — 지연 import

        df = bdq.getColumnInfo(TABLE)
        # 반환 스키마는 (name, type) 두 컬럼을 가정. 다르면 여기서 맞춘다.
        cols = df.columns.str.lower()
        name_col = df.columns[list(cols).index("name")] if "name" in list(cols) else df.columns[0]
        type_col = df.columns[list(cols).index("type")] if "type" in list(cols) else df.columns[1]
        self.columns = [
            ColumnInfo(str(r[name_col]), str(r[type_col]).upper())
            for _, r in df.iterrows()
        ]
        self.fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.save_cache()
        log.info("컬럼 카탈로그 갱신: %d개", len(self.columns))

    def ensure(self) -> None:
        """캐시가 있으면 쓰고, 없으면 최초 1회 조회."""
        if not self.load_cache():
            self.refresh()

    def get(self, name: str) -> ColumnInfo | None:
        return next((c for c in self.columns if c.name == name), None)

    def condition_columns(self) -> list[ColumnInfo]:
        """조건 빌더에 노출할 컬럼 (item_id·value는 제외)."""
        return [c for c in self.columns if c.name not in ("item_id", "value")]
