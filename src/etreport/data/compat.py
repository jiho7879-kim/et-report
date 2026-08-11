"""기존 DuckDB 호환 계층 — 손으로 만들어 쓰던 DB를 그대로 읽는다.

기준 테이블 이름은 **et_data**. 이 툴이 새로 만드는 DB도 같은 이름을 쓰므로
`select * from et_data`가 예전 그대로 동작한다.

컬럼 이름은 사람마다 다르므로 고정하지 않고 별칭 사전으로 찾아낸다.
long(item_id/value)과 wide(item이 컬럼) 두 형태 모두 인식한다.
분석 화면은 이 프로파일만 보고 동작하므로, 스키마가 달라도 읽기는 된다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import duckdb

log = logging.getLogger(__name__)

PREFERRED_TABLES = ("et_data", "fact")

# 역할 → 후보 컬럼명(소문자). 앞쪽이 우선.
ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "lot":   ("root_lot_id", "lot_id", "lot", "lotid", "lot_no"),
    "wafer": ("wafer_id", "wafer", "wafer_no", "waferid", "slot_no", "slot"),
    "x":     ("chip_x_pos", "die_x", "x_pos", "chip_x", "x"),
    "y":     ("chip_y_pos", "die_y", "y_pos", "chip_y", "y"),
    "temp":  ("temperature", "temp"),
    "step":  ("step_id", "step", "oper", "operation"),
    "seq":   ("step_seq", "seq", "retest_cnt"),
    "site":  ("total_site_cnt", "site_cnt", "site_no", "site"),
    "time":  ("tkout_time", "create_dttm", "meas_time", "test_time", "dttm"),
    "line":  ("line_id", "line", "fab", "fab_id"),
    "key":   ("key_hash", "key"),
    "item":  ("item_id", "item", "param", "parameter", "item_name"),
    "value": ("et_value", "value", "val", "result", "meas_value"),
}

NUMERIC_TYPES = {"DOUBLE", "FLOAT", "REAL", "DECIMAL", "HUGEINT",
                 "BIGINT", "INTEGER", "SMALLINT", "TINYINT"}


@dataclass
class TableProfile:
    table: str
    columns: dict[str, str]          # 컬럼명 → 타입(대문자)
    roles: dict[str, str] = field(default_factory=dict)   # 역할 → 실제 컬럼명
    items: list[str] = field(default_factory=list)        # wide일 때 item 컬럼
    is_long: bool = False

    @property
    def lot_col(self) -> str | None:
        return self.roles.get("lot")

    @property
    def wafer_col(self) -> str | None:
        return self.roles.get("wafer")

    def describe(self) -> str:
        shape = "long" if self.is_long else "wide"
        found = ", ".join(f"{k}={v}" for k, v in self.roles.items()
                          if k in ("lot", "wafer", "time"))
        return f"{self.table} ({shape}) · {found or '키 컬럼 자동인식 실패'}"


def list_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema','pg_catalog') "
        "ORDER BY table_name").fetchall()]


def pick_table(con: duckdb.DuckDBPyConnection,
               prefer: str | None = None) -> str | None:
    names = list_tables(con)
    lower = {n.lower(): n for n in names}
    for cand in ((prefer,) if prefer else ()) + PREFERRED_TABLES:
        if cand and cand.lower() in lower:
            return lower[cand.lower()]
    # 뷰·메타 테이블을 빼고 남은 것 중 가장 컬럼이 많은 것
    meta = {"load_log", "exclusions"}
    rest = [n for n in names if n.lower() not in meta]
    if not rest:
        return None
    widths = {n: con.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_name=?",
        [n]).fetchone()[0] for n in rest}
    return max(rest, key=lambda n: widths[n])


def profile(con: duckdb.DuckDBPyConnection, table: str) -> TableProfile:
    cols = {r[0]: str(r[1]).upper() for r in con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name=? ORDER BY ordinal_position", [table]).fetchall()}
    p = TableProfile(table=table, columns=cols)
    lower = {c.lower(): c for c in cols}
    for role, cands in ROLE_ALIASES.items():
        for cand in cands:
            if cand in lower:
                p.roles[role] = lower[cand]
                break

    p.is_long = "item" in p.roles and "value" in p.roles
    if not p.is_long:
        used = set(p.roles.values())
        p.items = [c for c, t in cols.items()
                   if c not in used
                   and t.split("(")[0] in NUMERIC_TYPES]
    return p


def key_expr(p: TableProfile) -> str:
    """행을 구분할 키 식 — 있으면 key_hash, 없으면 키 컬럼 해시."""
    if "key" in p.roles:
        return f'"{p.roles["key"]}"'
    parts = [p.roles[r] for r in ("lot", "wafer", "x", "y", "temp",
                                  "step", "seq", "site", "time")
             if r in p.roles]
    if not parts:
        return "md5(CAST(rowid AS VARCHAR))"
    joined = ", ".join(f'CAST("{c}" AS VARCHAR)' for c in parts)
    return f"md5(concat_ws('|', {joined}))"


def select_sql(p: TableProfile, dedup_latest: bool = True) -> str:
    """분석용 wide SELECT — key/lot/wafer/gid + item 컬럼들.

    long이면 PIVOT으로 wide화한다. time 컬럼이 있으면 재측정(retest)의
    최신 행만 남긴다.
    """
    lot = p.roles.get("lot")
    waf = p.roles.get("wafer")
    lot_sel = f'"{lot}" AS lot' if lot else "'(lot?)' AS lot"
    waf_sel = f'"{waf}" AS wafer' if waf else "'(wafer?)' AS wafer"
    key = key_expr(p)

    if p.is_long:
        item, val = p.roles["item"], p.roles["value"]
        group = [c for c in (lot, waf) if c] + [
            p.roles[r] for r in ("x", "y", "temp", "step", "seq", "site", "time")
            if r in p.roles]
        gcols = ", ".join(f'"{c}"' for c in group) or "1"
        base = (f'SELECT {gcols}, "{item}" AS item_id, "{val}" AS value '
                f'FROM "{p.table}"')
        return (f"WITH src AS ({base}) "
                f"PIVOT src ON item_id USING first(value) GROUP BY {gcols}")

    items = ", ".join(f'"{c}"' for c in p.items)
    sql = (f'SELECT {key} AS key, {lot_sel}, {waf_sel}, \'\' AS gid'
           f'{", " + items if items else ""} FROM "{p.table}"')
    if dedup_latest and "time" in p.roles and lot and waf:
        keys = [p.roles[r] for r in ("x", "y", "temp", "step", "site")
                if r in p.roles]
        part = ", ".join(f'"{c}"' for c in [lot, waf, *keys])
        sql += (f' QUALIFY row_number() OVER (PARTITION BY {part} '
                f'ORDER BY "{p.roles["time"]}" DESC) = 1')
    return sql
