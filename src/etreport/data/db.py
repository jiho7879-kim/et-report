"""DuckDB 저장소.

et_keys         : 키 9종 + line_id + key_hash — 측정 행 하나에 한 줄
et_items_NNN    : key_hash + item 컬럼 **최대 ITEMS_PER_TABLE개**. 넘치면 다음 번호
et_data (뷰)    : et_keys LEFT JOIN et_items_* — `select * from et_data`가 예전처럼
                  키 + item 전부를 돌려준다. 읽는 코드는 이 뷰만 본다.
load_log        : 파일 단위 적재 이력(1단 중복 차단)
뷰             : et_data → v_latest(retest 최신)

**왜 테이블을 쪼개나**(2026-10 OOM). DuckDB는 열 단위로 저장해서, 이미 있는
테이블에 행을 이어 붙이고 디스크에 쓸 때(checkpoint) **열마다** 256KB 블록을
붙잡는다 — 쓰기 메모리가 행 수가 아니라 **열 수**에 비례한다(실측 약 1MB/열).
item 5,000개짜리 한 테이블은 500행만 이어 붙여도 4GB 상한에서 죽었고, 500열
테이블은 1GB 상한에서도 버틴다. 그래서 한 테이블의 열 수에 천장을 둔다 — 1년치
든 item이 몇 개든 적재 메모리가 일정하다. 읽기는 뷰의 projection pushdown으로
고른 item이 든 테이블의 열만 읽는다.

예전 형식(et_data·fact가 **테이블**인 DB)은 읽기와 이어 적재 모두 예전 경로를
그대로 탄다 — 다만 item이 수천 개면 그 경로는 적재 OOM이 날 수 있으니 새 파일로
적재하는 것이 맞다(로그에 남긴다).

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
TABLE = "et_data"                    # 손코딩 시절과 동일한 이름(새 DB는 뷰)
LEGACY_TABLES = ("fact",)            # 예전 버전이 만든 DB도 읽는다
KEYS_TABLE = "et_keys"
ITEMS_PREFIX = "et_items_"
#: 테이블 하나의 item 열 상한 — 적재 메모리를 정하는 다이얼(위 docstring).
ITEMS_PER_TABLE = 500
KEY_COLS = (*KEY9, "line_id", "key_hash")


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


def _duck_type(dt: pl.DataType) -> str:
    return "DOUBLE" if dt in (pl.Float64, pl.Float32) else "VARCHAR"


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
        self.filled = 0                  # 겹친 행의 빈 칸을 채운 수(§6)
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
        """존재하는 데이터 테이블(또는 뷰) 이름 (et_data 우선, 없으면 예전 이름)."""
        for t in (TABLE, *LEGACY_TABLES):
            if self.con.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name=?",
                    [t]).fetchone():
                return t
        return None

    def _legacy_table(self) -> str | None:
        """예전 형식(한 테이블에 item 전부)이면 그 이름. 새 형식·빈 DB면 None."""
        for t in (TABLE, *LEGACY_TABLES):
            if self.con.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name=? AND table_type='BASE TABLE'", [t]).fetchone():
                return t
        return None

    def _columns(self, tbl: str) -> dict[str, str]:
        return dict(self.con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name=? ORDER BY ordinal_position", [tbl]).fetchall())

    def _item_tables(self) -> list[str]:
        return [r[0] for r in self.con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE ? AND table_type='BASE TABLE' ORDER BY 1",
            [ITEMS_PREFIX + "%"]).fetchall()]

    def _evolve(self, tbl: str, schema: dict, cols: list[str]) -> None:
        """`cols`가 `tbl`에 들어갈 수 있게 컬럼을 더하거나 VARCHAR로 넓힌다.

        `schema`는 `dict(wide.schema)` — polars가 `.schema`를 부를 때마다 열 전체로
        새로 만들어서, 열마다 부르면 item 3,000개에서 그것만 40초였다.
        """
        have = self._columns(tbl)
        for col in cols:
            dt = schema[col]
            if col in have and dt == pl.Utf8 and have[col] != "VARCHAR":
                # 예전 DB는 step_seq가 INTEGER다. 문자열 코드(`LN08…`)를
                # 그대로 INSERT하면 변환 오류로 적재가 통째로 실패한다.
                # 숫자 값은 VARCHAR로 바꿔도 key_hash가 같다(`"1"`).
                self.con.execute(
                    f'ALTER TABLE "{tbl}" ALTER COLUMN "{col}" TYPE VARCHAR')
            elif col not in have:
                self.con.execute(
                    f'ALTER TABLE "{tbl}" ADD COLUMN "{col}" {_duck_type(dt)}')

    # ── 적재 ──────────────────────────────────────────────────
    def load_wide(self, wide: pl.DataFrame, src_file: str) -> tuple[int, int]:
        """버킷 하나 분량의 wide를 dedup 후 적재. 신규 item 컬럼은 자동 추가.

        반환값은 `(새로 들어간 행 수, 빈 칸을 채운 행 수)`. 화면의 '적재 완료 —
        N행'이 앞의 값이므로, 같은 파일을 그대로 다시 적재하면 (0, 0)이 나온다.

        **뒤의 값이 있는 이유**(§6). 예전에는 key_hash가 이미 있으면 ANTI JOIN이
        그 행을 통째로 버렸다. 그래서 리포메터에 item을 더해 같은 기간을 다시
        적재하면 `ALTER TABLE`로 컬럼은 생기는데 값을 넣을 행이 하나도 남지 않아
        **새 item이 전부 NULL인 채로** DB에 박혔다 — 화면에서는 "적재는 됐다는데
        표도 산점도도 비어 있다"로 보였고, step_seq가 갈려 기록되는 항목에서
        특히 티가 났다(합쳐지지 않는 seq 쪽이 통째로 빈다). 이제는 겹치는 행의
        **빈 칸만** 채운다. 이미 값이 있는 칸은 건드리지 않으므로 멱등이고,
        예전처럼 재적재로 값이 뒤집히는 일도 없다.
        """
        if wide.is_empty():
            return 0, 0
        legacy = self._legacy_table()
        self.con.begin()                 # 정렬 검사가 실패하면 통째로 되돌린다
        try:
            if legacy is not None:
                self.con.register("incoming", wide.to_arrow())
                inserted, filled = self._load_legacy(legacy, wide)
            else:
                inserted, filled = self._load_split(wide)
            self.con.execute(
                "INSERT OR REPLACE INTO load_log VALUES (?, ?, ?, '')",
                [src_file, datetime.now(), inserted])
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise
        finally:
            self.con.unregister("incoming")
        return inserted, filled

    def _load_split(self, wide: pl.DataFrame) -> tuple[int, int]:
        """새 형식 — 키는 et_keys, item은 ITEMS_PER_TABLE개씩 나눈 테이블로.

        **모든 테이블이 같은 행 순서를 지킨다** — et_keys의 n번째 행과 각 item
        테이블의 n번째 행이 같은 측정이다. 그래서 et_data 뷰가 해시 조인 대신
        `POSITIONAL JOIN`(행 번호로 나란히 붙이기)으로 스트리밍된다. LEFT JOIN으로
        붙이던 첫 시안은 `SELECT *`(미리보기·내보내기)가 item 테이블 전부를 해시
        테이블에 올려 1년치에서 2GB 상한을 넘었다.

        순서를 지키는 규칙은 셋이다: 새 키는 `_new`에 순번을 박아 **모든** 테이블에
        같은 순번으로 넣고(그 load에 item이 없는 테이블에도 key_hash만), 새로 여는
        테이블은 et_keys를 그대로 따라 채우고, 행은 지우지 않는다(UPDATE는 제자리).
        한 load가 끝나면 key_hash를 나란히 대조해 하나라도 어긋나면 롤백한다 —
        어긋난 채로 붙이면 값이 남의 행에 조용히 들어가기 때문이다.
        """
        wide = wide.unique("key_hash", keep="first", maintain_order=True)
        schema = dict(wide.schema)
        keys = [c for c in wide.columns if c in KEY_COLS]
        items = [c for c in wide.columns if c not in KEY_COLS]
        self.con.register("incoming", wide.to_arrow())
        if self.table_name() is None:
            sel = ", ".join(f'"{c}"' for c in keys)
            self.con.execute(f'CREATE TABLE "{KEYS_TABLE}" AS '
                             f"SELECT {sel} FROM incoming LIMIT 0")
        else:
            self._evolve(KEYS_TABLE, schema, keys)

        # item → 테이블. 처음 보는 item은 마지막 테이블에 자리가 있으면 거기,
        # 없으면 새 테이블을 연다. 한 번 정한 자리는 바뀌지 않는다.
        tables = self._item_tables()
        where = {c: t for t in tables for c in self._columns(t)
                 if c != "key_hash"}
        width = {t: sum(1 for v in where.values() if v == t) for t in tables}
        fresh: dict[str, list[str]] = {}       # 이번에 새로 여는 테이블 → 열
        for col in items:
            if col in where:
                continue
            if not tables or width[tables[-1]] >= ITEMS_PER_TABLE:
                tables.append(f"{ITEMS_PREFIX}{len(tables):03d}")
                width[tables[-1]] = 0
                fresh[tables[-1]] = []
            where[col] = tables[-1]
            width[tables[-1]] += 1
            if tables[-1] in fresh:
                fresh[tables[-1]].append(col)
        # 새 테이블은 열을 한 번에 만들고(ALTER를 열마다 부르면 item 수천 개에서
        # 카탈로그 변경만 수십 초다) 이미 있는 키를 et_keys 순서 그대로 채운다
        for tbl, cols in fresh.items():
            defs = ", ".join(f'"{c}" {_duck_type(schema[c])}' for c in cols)
            self.con.execute(f'CREATE TABLE "{tbl}"(key_hash VARCHAR, {defs})')
            self.con.execute(f'INSERT INTO "{tbl}"(key_hash) '
                             f'SELECT key_hash FROM "{KEYS_TABLE}"')
        by_table: dict[str, list[str]] = {t: [] for t in tables}
        for col in items:
            by_table[where[col]].append(col)

        # 처음 보는 키 — 순번을 한 번 박아 두고 모든 테이블이 그 순서를 따른다
        self.con.execute(
            'CREATE OR REPLACE TEMP TABLE _new AS SELECT key_hash, '
            'row_number() OVER () AS ord FROM (SELECT i.key_hash FROM incoming i '
            f'ANTI JOIN "{KEYS_TABLE}" f USING(key_hash))')
        filled: set[str] = set()
        for tbl, cols in by_table.items():
            self._evolve(tbl, schema, cols)
            if len(wide) > self._count("_new"):   # 겹친 행이 있다 = 채울 것이 있다
                filled |= self._fill_gaps(tbl, cols)
            self._append_new(tbl, cols)
        self._append_new(KEYS_TABLE, [c for c in keys if c != "key_hash"])
        inserted = self._count("_new")
        self._rebuild_data_view(tables)     # 컬럼이 늘거나 타입이 바뀌었을 수 있다
        self._check_aligned(tables)
        return inserted, len(filled)

    def _count(self, tbl: str) -> int:
        return int(self.con.execute(f'SELECT count(*) FROM "{tbl}"').fetchone()[0])

    def _append_new(self, tbl: str, cols: list[str]) -> None:
        """`_new`의 키를 순번대로 `tbl`에 붙인다(값은 incoming에서)."""
        sel = "".join(f', i."{c}"' for c in cols)
        self.con.execute(
            f'INSERT INTO "{tbl}" BY NAME SELECT n.key_hash{sel} FROM _new n '
            f'JOIN incoming i USING(key_hash) ORDER BY n.ord')

    def _check_aligned(self, tables: list[str]) -> None:
        """모든 item 테이블의 n번째 key_hash가 et_keys의 n번째와 같은지."""
        for tbl in tables:
            bad = self.con.execute(
                f'SELECT count(*) FROM (SELECT key_hash FROM "{KEYS_TABLE}") a '
                f'POSITIONAL JOIN (SELECT key_hash AS k FROM "{tbl}") b '
                f"WHERE a.key_hash IS DISTINCT FROM b.k").fetchone()[0]
            if bad:
                raise RuntimeError(
                    f"DB 행 정렬이 어긋났습니다({tbl}, {bad}행) — 이번 적재를 "
                    f"되돌립니다. 새 DB 파일로 다시 적재하세요.")

    def _rebuild_data_view(self, tables: list[str]) -> None:
        """et_data = 키 + 모든 item 테이블을 **행 번호로** 나란히(POSITIONAL JOIN).

        스키마가 바뀔 때마다 다시 만든다 — DuckDB는 뷰의 `*`를 만들 때 펼쳐 두어서
        컬럼 목록이 낡는다.
        """
        joins = "".join(f' POSITIONAL JOIN (SELECT * EXCLUDE (key_hash) '
                        f'FROM "{t}")' for t in tables)
        self.con.execute(f'CREATE OR REPLACE VIEW "{TABLE}" AS '
                         f'SELECT * FROM "{KEYS_TABLE}"{joins}')

    def _load_legacy(self, tbl: str, wide: pl.DataFrame) -> tuple[int, int]:
        """예전 형식(한 테이블) DB에 이어 적재 — 예전 동작 그대로."""
        if len(wide.columns) > ITEMS_PER_TABLE:
            log.warning("예전 형식 DB(%s)에 item %d개를 이어 적재합니다 — 메모리가 "
                        "부족하면 새 DB 파일로 적재하세요", tbl, len(wide.columns))
        self._evolve(tbl, dict(wide.schema), wide.columns)
        # 2단: 행 단위 중복 차단. DuckDB는 INSERT 결과로 넣은 행 수를 준다.
        got = self.con.execute(
            f'INSERT INTO "{tbl}" BY NAME SELECT i.* FROM incoming i '
            f'ANTI JOIN "{tbl}" f USING(key_hash)').fetchone()
        inserted = int(got[0]) if got else 0
        filled: set[str] = set()
        if inserted < len(wide):          # 겹친 행이 있다 = 채울 것이 있다
            skip = {*KEY9, "line_id", "key_hash"}
            filled = self._fill_gaps(tbl, [c for c in wide.columns
                                           if c not in skip])
        return inserted, len(filled)

    def _fill_gaps(self, tbl: str, cols: list[str]) -> set[str]:
        """이미 있는 key_hash 행의 **빈 item 칸만** 채운다(§6). 채운 key들을 돌려준다.

        `COALESCE(f.x, i.x)`라 이미 값이 있는 칸은 그대로다 — 재적재로 숫자가
        바뀌지 않는다. WHERE에 '채울 것이 하나라도 있는 행'만 남기는 이유는 두
        가지다: 같은 파일을 다시 적재했을 때 0을 돌려줘야 화면이 거짓말을 하지
        않고, 안 바뀔 행을 전부 다시 쓰지 않는다. key 집합으로 돌려주는 것은
        item 테이블이 여럿일 때 한 행을 여러 번 세지 않기 위해서다.
        """
        if not cols:
            return set()
        sets = ", ".join(f'"{c}" = COALESCE(f."{c}", i."{c}")' for c in cols)
        gap = " OR ".join(f'(f."{c}" IS NULL AND i."{c}" IS NOT NULL)'
                          for c in cols)
        return {r[0] for r in self.con.execute(
            f'UPDATE "{tbl}" AS f SET {sets} FROM incoming i '
            f'WHERE f.key_hash = i.key_hash AND ({gap}) '
            f'RETURNING f.key_hash').fetchall()}

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


def pivot_wide(part: pl.DataFrame) -> pl.DataFrame:
    """long → wide. `part.pivot(..., aggregate_function="first")`와 같은 결과.

    polars pivot은 **만드는 열 수**에 비례해 느려서 item 5,000개 하루치에 12초가
    걸렸다(적재 시간의 절반). 값이 실수면 (키 × item) 배열에 한 번에 흩뿌린다 —
    (키, item)마다 첫 값만 남긴 뒤라 덮어쓰기 순서와 무관하다. 문자열 값은
    예전 pivot 그대로 간다.
    """
    idx = [*KEY9, "line_id", "key_hash"]
    if not part.schema["value"].is_float():
        return part.pivot(on="item_id", index=idx, values="value",
                          aggregate_function="first")
    import numpy as np

    part = part.unique(subset=["key_hash", "item_id"], keep="first",
                       maintain_order=True)
    keys = part.select(idx).unique(subset="key_hash", keep="first",
                                   maintain_order=True)
    items = part["item_id"].unique(maintain_order=True)
    pos = part.select("key_hash", "item_id", "value").join(
        keys.select("key_hash").with_row_index("ki"), on="key_hash",
        nulls_equal=True).join(
        items.to_frame().with_row_index("ii"), on="item_id", nulls_equal=True)
    grid = np.full((keys.height, len(items)), np.nan)
    grid[pos["ki"].to_numpy(), pos["ii"].to_numpy()] = \
        pos["value"].cast(pl.Float64).to_numpy()
    # NaN → NULL: 원래 값이 NULL이었거나 그 키에 없던 item이다(리포메팅이
    # 비유한 값은 이미 걸러 냈으므로 진짜 NaN은 없다).
    vals = pl.from_numpy(grid, schema=[str(i) for i in items], orient="row")
    return keys.hstack(vals.fill_nan(None))


def pivot_and_load(store: Store, parquet_files: list[Path],
                   on_progress=None) -> int:
    """long parquet들 → key_hash 버킷 재파티션 → 버킷별 피벗 → 적재.

    반환은 **새로 들어간 행 수**다. 겹친 행의 빈 칸을 채운 수는
    `store.filled`에 쌓인다(§6) — 같은 기간을 item만 더해 다시 적재하면
    반환값이 0인데 실제로는 값이 들어가므로, 화면이 그 둘을 함께 적어야 한다.

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
    frame = lazy.collect()
    if frame.is_empty():
        return 0
    # 해시는 **키마다 한 번**만 — long은 item 수만큼 같은 키가 반복된다(item
    # 5,000개면 5,000번). 행마다 부르면 하루치에 20초가 그 UDF였다. 식은 그대로라
    # 값도 그대로다(key_hash_expr 주석 참조).
    hashed = (frame.select(KEY9).unique()
              .with_columns(key_hash=key_hash_expr()))
    frame = frame.join(hashed, on=KEY9, how="left", nulls_equal=True)

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
            wide = pivot_wide(part)
            n, f = store.load_wide(wide, src_file=f"bucket_{b}")
            total, store.filled = total + n, store.filled + f
        if on_progress:
            on_progress(b + 1, n_buckets)
    for p in parquet_files:
        store.con.execute(
            "INSERT OR REPLACE INTO load_log VALUES (?, ?, 0, 'file')",
            [p.name, datetime.now()])
    store.rebuild_views()
    return total
