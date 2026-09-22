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
    # fab tracking·계측 조회의 기본값을 DB에서 뽑을 때 쓴다(data/lotcontext.py).
    # 값 컬럼이 아니므로 item 인식에서 빠지는 것이 맞다 — 원래도 문자열이라
    # 숫자 item으로 잡히지 않았고, 새로 만든 문자열 폴백에서도 빠진다.
    "process": ("process_id", "process", "proc_id"),
    "part":  ("part_id", "part", "product_id", "device_id"),
    "key":   ("key_hash", "key"),
    "item":  ("item_id", "item", "param", "parameter", "item_name"),
    "value": ("et_value", "value", "val", "result", "meas_value"),
}

NUMERIC_TYPES = {"DOUBLE", "FLOAT", "REAL", "DECIMAL", "HUGEINT",
                 "BIGINT", "INTEGER", "SMALLINT", "TINYINT"}
#: 문자열 타입 — 예전 DB는 온도·측정값까지 여기에 들어 있다. 읽을 때 TRY_CAST로
#: 숫자를 꺼낸다(적재는 여전히 숫자만 받는다).
TEXT_TYPES = {"VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR"}

# step_seq만 다른 행을 **한 측정점으로 합칠 때**의 그룹 키(계획서 §10.1).
# x는 step_seq=1, y는 step_seq=2에 기록되는 경우가 흔하다. seq를 키에 두면
# 두 행으로 갈려 x·y가 함께 있는 행이 0개가 되고 산점도가 통째로 빈다.
# 단 step_id·온도·site_cnt가 다르면 **다른 측정점**이므로 절대 합치지 않는다.
MERGE_ROLES = ("x", "y", "temp", "step", "site")

# 분석 프레임에 함께 싣는 측정 조건 — 그룹 편집 4단 필터(§9.1)가 쓴다.
CTX_ROLES = ("step", "temp", "site")

TEMP_STEP = 5          # 측정 온도는 5단위 정수(-40·25·45·85·125·150)로 다룬다


def temp_expr(col: str) -> str:
    """온도를 **가장 가까운 5의 배수 정수**로 — 23.9→25, 149→150. NULL은 그대로.

    DB에는 계측기가 준 raw 온도(23.9, 24.9…)가 그대로 들어 있다. 적재가 아니라
    **읽는 시점**에 보정한다(ABSOLUTE를 로딩 때 다시 거는 것과 같은 이유) —
    이미 쌓인 DB도 화면·표·PPT가 맞아야 하고, 보정 경로를 두 곳에 두지 않는다.
    병합·필터·배정 비교가 모두 이 값을 쓰므로 **한 군데라도 빠뜨리면 어긋난다.**

    **TRY_CAST를 거치는 이유**: 예전에 손으로 만든 DB는 temperature가 VARCHAR다.
    문자열에 산술을 걸면 DuckDB가 암시적 캐스팅을 시도하다 `'25C'`·`''` 같은 값
    하나에서 `Conversion Error`로 조회 전체를 떨어뜨린다 — 그러면 **DB가 통째로
    안 열려서** 표도 plot도 못 본다. TRY_CAST는 못 읽는 값만 NULL로 두고 나머지
    행을 살린다. 숫자 컬럼에서는 결과가 예전과 완전히 같다(캐스팅이 무손실).
    """
    return (f'CAST(ROUND(TRY_CAST("{col}" AS DOUBLE) / {TEMP_STEP}) AS BIGINT)'
            f" * {TEMP_STEP}")


def _ctx_col(role: str, col: str) -> str:
    """조건 컬럼 하나를 표준 이름으로. 온도만 5단위 보정을 거친다."""
    return (f"{temp_expr(col)} AS {role}" if role == "temp"
            else f'"{col}" AS {role}')


def _quote_lot(v: object) -> str:
    """lot ID 하나를 SQL 문자열 리터럴로. 작은따옴표는 두 번 적어 닫는다.

    사용자가 고른 값이 SQL 문자열로 들어가는 **유일한 자리**다. lot ID에 따옴표가
    들어갈 일은 없지만, 없다고 가정하지 않는다.
    """
    return "'" + str(v).replace("'", "''") + "'"


def lot_filter(p: TableProfile, lots: list[str] | None) -> str:
    """`WHERE lot IN (…)` 조각. 좁힐 것이 없으면 **빈 문자열**.

    빈 문자열이라는 점이 중요하다 — lot을 고르지 않은 DB에서는 예전과 글자 하나까지
    같은 SQL이 나와야 지금까지의 동작(과 그걸 고정한 테스트)이 그대로 산다.
    lot 컬럼을 인식하지 못한 스키마에서도 조용히 좁히지 않는다.
    """
    col = p.roles.get("lot")
    if not lots or not col:
        return ""
    vals = ", ".join(_quote_lot(v) for v in dict.fromkeys(lots))
    return f' WHERE "{col}" IN ({vals})'


@dataclass
class TableProfile:
    table: str
    columns: dict[str, str]          # 컬럼명 → 타입(대문자)
    roles: dict[str, str] = field(default_factory=dict)   # 역할 → 실제 컬럼명
    items: list[str] = field(default_factory=list)        # wide일 때 item 컬럼
    is_long: bool = False
    #: 숫자로 읽어야 하지만 DB에는 문자열로 들어 있는 컬럼(예전 DB). 읽을 때
    #: TRY_CAST를 씌운다 — `numeric_expr()` 참조.
    text_numeric: set[str] = field(default_factory=set)

    def numeric_expr(self, col: str) -> str:
        """숫자로 다뤄야 하는 컬럼 하나의 SELECT 식.

        예전에 손으로 만든 DB는 값이 VARCHAR로 들어 있는 일이 있다. 그대로 읽으면
        polars 쪽에서 문자열 컬럼이 되어 plot·표가 통째로 비고, 산술을 걸면
        DuckDB가 값 하나 때문에 조회 전체를 떨어뜨린다. **못 읽는 값만 NULL로
        두고 나머지 행은 살린다** — 숫자 컬럼에서는 예전과 글자까지 같은 SQL이다.
        """
        return (f'TRY_CAST("{col}" AS DOUBLE) AS "{col}"'
                if col in self.text_numeric else f'"{col}"')

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
    if p.is_long:
        # long의 value가 문자열인 예전 DB — PIVOT 결과가 문자열 컬럼이 되어
        # plot이 통째로 빈다. 읽을 때만 숫자로 바꾼다.
        if _is_text(cols.get(p.roles["value"], "")):
            p.text_numeric.add(p.roles["value"])
        return p

    used = set(p.roles.values())
    rest = {c: t for c, t in cols.items() if c not in used}
    p.items = [c for c, t in rest.items() if t.split("(")[0] in NUMERIC_TYPES]
    if not p.items:
        # **숫자 컬럼이 하나도 없다** = 값이 전부 VARCHAR로 적재된 예전 DB.
        # 여기서 포기하면 "item이 0개"라 표도 plot도 그릴 수 없다. 남은 컬럼을
        # item으로 보고 읽을 때 TRY_CAST를 씌운다 — 진짜 문자열 컬럼은 전부
        # NULL이 되어 조용히 빠질 뿐, 읽기 자체를 막지는 않는다.
        p.items = [c for c, t in rest.items() if _is_text(t)]
        p.text_numeric.update(p.items)
        if p.items:
            log.warning("%s: 숫자 item 컬럼이 없어 문자열 %d개를 숫자로 읽습니다",
                        table, len(p.items))
    return p


def _is_text(duck_type: str) -> str:
    return duck_type.split("(")[0] in TEXT_TYPES


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


def merge_cols(p: TableProfile) -> list[str]:
    """step_seq 병합의 그룹 키가 될 실제 컬럼명 (lot·wafer 제외)."""
    return [p.roles[r] for r in MERGE_ROLES if r in p.roles]


def merges_seq(p: TableProfile) -> bool:
    """step_seq 병합을 적용할지.

    seq 컬럼이 있고 lot·wafer와 병합 키가 최소 하나는 인식됐을 때만 합친다.
    키가 없는데 그룹핑을 하면 합칠 이유도 없이 행이 뭉쳐(예: wafer 하나가
    한 점이 되어) 조용히 데이터를 잃는다 — 못 합치는 것보다 나쁘다.
    """
    return bool("seq" in p.roles and p.roles.get("lot") and p.roles.get("wafer")
                and merge_cols(p))


def ctx_select(p: TableProfile) -> list[str]:
    """측정 조건 컬럼(step·temp·site)을 표준 이름으로 함께 싣는다.

    그룹 편집의 4단 연쇄 필터(§9.1)가 이 값으로 좁히고, 배정도 그 범위에만
    적용한다 — 같은 wafer라도 step·온도가 다르면 다른 측정점이기 때문이다.
    없는 스키마에서도 프레임 모양이 흔들리지 않게 NULL로라도 자리를 만든다.
    """
    return [_ctx_col(r, p.roles[r]) if r in p.roles else f"NULL AS {r}"
            for r in CTX_ROLES]


def wafer_index_sql(p: TableProfile, lots: list[str] | None = None) -> str:
    """(lot, wafer, step, temp, site) → 포인트 수. 그룹 편집의 조회용.

    item 컬럼을 전혀 건드리지 않으므로 [적용] 없이도 가볍게 돌릴 수 있다
    (§9.1 — 대부분의 사용자는 DB만 고르고 그룹부터 짠다).
    """
    lot = p.roles.get("lot")
    waf = p.roles.get("wafer")
    lot_sel = f'"{lot}" AS lot' if lot else "'(lot?)' AS lot"
    waf_sel = f'"{waf}" AS wafer' if waf else "'(wafer?)' AS wafer"
    x, y = p.roles.get("x"), p.roles.get("y")
    if x and y:      # long이든 wide든 die 좌표로 세면 포인트 수가 맞는다
        n = f"count(DISTINCT concat_ws('|', \"{x}\", \"{y}\")) AS n"
    else:
        n = "count(*) AS n"
    sel = ", ".join([lot_sel, waf_sel, *ctx_select(p), n])
    return f'SELECT {sel} FROM "{p.table}"{lot_filter(p, lots)} GROUP BY ALL'


def lot_index_sql(p: TableProfile) -> str:
    """lot → wafer 장수. 도크 lot 목록이 쓴다.

    item도 die 좌표도 건드리지 않아 큰 DB에서도 한 번 훑고 끝난다 — DB를 고르는
    즉시(=[적용] 전에) 도는 조회라 가벼워야 한다.
    """
    lot = p.roles.get("lot")
    if not lot:
        return ""
    waf = p.roles.get("wafer")
    n = f'count(DISTINCT "{waf}") AS wafers' if waf else "0 AS wafers"
    return f'SELECT "{lot}" AS lot, {n} FROM "{p.table}" GROUP BY 1 ORDER BY 1'


def select_sql(p: TableProfile, dedup_latest: bool = True,
               lots: list[str] | None = None) -> str:
    """분석용 wide SELECT — key/lot/wafer/gid + step/temp/site + item 컬럼들.

    long이면 PIVOT으로 wide화한다. time 컬럼이 있으면 재측정(retest)의
    최신 행만 남기되, **파티션에 step_seq를 포함**한다 — 빼면 seq가 다른
    정상 행까지 하나만 남고 나머지가 사라진다(§10.1).

    그다음 step_seq(과 측정 시각)만 다른 행을 한 측정점으로 합친다. 각 item은
    NULL이 아닌 첫 값(any_value)을 취하므로, x가 seq 1·y가 seq 2에 기록돼
    있어도 한 행에 함께 실린다. key는 합친 행들의 최솟값을 쓴다 — seq가 하나뿐인
    (=지금까지 정상 동작하던) DB에서는 예전 key와 값이 같아서 제외 사이드카가
    그대로 살아 있다.

    `lots`를 주면 그 lot만 읽는다. **WHERE는 QUALIFY보다 앞**이라 retest 판정도
    좁힌 범위 안에서 돈다 — 고르지 않은 lot의 재측정 행이 남은 lot의 순위를
    흔들지 않는다.
    """
    lot = p.roles.get("lot")
    waf = p.roles.get("wafer")
    lot_sel = f'"{lot}" AS lot' if lot else "'(lot?)' AS lot"
    waf_sel = f'"{waf}" AS wafer' if waf else "'(wafer?)' AS wafer"
    key = key_expr(p)
    merge = merges_seq(p)
    where = lot_filter(p, lots)

    if p.is_long:
        item, val = p.roles["item"], p.roles["value"]
        roles = MERGE_ROLES if merge else ("x", "y", "temp", "step",
                                           "seq", "site", "time")
        group = [c for c in (lot, waf) if c] + [
            p.roles[r] for r in roles if r in p.roles]
        temp_col = p.roles.get("temp")
        # 온도는 보정한 값으로 묶는다 — raw 23.9와 25.0이 다른 측정점으로
        # 갈라지면 §10.1 병합이 무의미해진다
        sel = ", ".join(f'{temp_expr(c)} AS "{c}"' if c == temp_col else f'"{c}"'
                        for c in group) or "1"
        gcols = ", ".join(f'"{c}"' for c in group) or "1"
        # value가 VARCHAR인 예전 DB면 여기서 숫자로 꺼낸다(numeric_expr).
        val_sel = p.numeric_expr(val).replace(f'AS "{val}"', "AS value") \
            if val in p.text_numeric else f'"{val}" AS value'
        base = (f'SELECT {sel}, "{item}" AS item_id, {val_sel} '
                f'FROM "{p.table}"{where}')
        return (f"WITH src AS ({base}) "
                f"PIVOT src ON item_id USING any_value(value) GROUP BY {gcols}")

    items = [p.numeric_expr(c) for c in p.items]
    has_time = dedup_latest and "time" in p.roles and lot and waf
    rank = ""
    if has_time:
        keys = [p.roles[r] for r in ("x", "y", "temp", "step", "site", "seq")
                if r in p.roles]
        part = ", ".join(f'"{c}"' for c in [lot, waf, *keys])
        rank = (f'row_number() OVER (PARTITION BY {part} '
                f'ORDER BY "{p.roles["time"]}" DESC)')

    if not merge:
        sel = ", ".join([f"{key} AS key", lot_sel, waf_sel, "'' AS gid",
                         *ctx_select(p), *items])
        dedup = f" QUALIFY {rank} = 1" if rank else ""
        return f'SELECT {sel} FROM "{p.table}"{where}{dedup}'

    temp_col = p.roles.get("temp")
    # 병합 키에 들어가기 **전에** 온도를 보정한다(23.9와 25.0을 한 점으로)
    mc_sel = [f'{temp_expr(c)} AS "{c}"' if c == temp_col else f'"{c}"'
              for c in merge_cols(p)]
    mc = [f'"{c}"' for c in merge_cols(p)]
    # **병합 경로에서는 retest를 QUALIFY로 지우지 않는다.** 파티션에 step_seq가
    # 들어 있어도 그 값이 NULL이면(= 적재 당시 seq를 못 받아 온 DB) seq가 다른
    # 두 행이 한 파티션에 들어가 늦은 쪽만 남고 **이른 쪽 item이 통째로
    # 사라진다** — x는 있는데 y가 없어 산점도도 요약 표도 빈다. 대신 값은
    # `arg_max(item, 시각)`으로 뽑는다: item마다 **NULL이 아닌 값 중 가장 늦은
    # 것**을 고르므로 retest(같은 item 재측정 → 최신 값)와 seq 분산(서로 다른
    # item → 한 행에 모임)이 같은 식 하나로 풀린다.
    inner_sel = [f"{key} AS key", lot_sel, waf_sel, *mc_sel, *items]
    if has_time:
        inner_sel.append(f"{rank} AS __rn")
        inner_sel.append(f'"{p.roles["time"]}" AS __t')
    inner = f'SELECT {", ".join(inner_sel)} FROM "{p.table}"{where}'
    # step·temp·site는 병합 그룹 키이므로 집계 없이 그대로 뽑을 수 있다
    ctx = [f'"{p.roles[r]}" AS {r}' if r in p.roles else f"NULL AS {r}"
           for r in CTX_ROLES]
    # 바깥에서는 **이름만** 쓴다 — 안쪽에서 이미 TRY_CAST가 걸려 별칭이 붙었다.
    names = [f'"{c}"' for c in p.items]
    # key는 예전과 같은 집합(= seq마다 최신 행)의 최솟값이어야 한다 — 제외
    # 포인트 사이드카가 이 값으로 저장돼 있어서, 바뀌면 제외 이력이 사라진다.
    key_agg = "min(key) FILTER (WHERE __rn = 1) AS key" if has_time \
        else "min(key) AS key"
    val_agg = [f"arg_max({c}, __t) AS {c}" for c in names] if has_time \
        else [f"any_value({c}) AS {c}" for c in names]
    outer = ", ".join([key_agg, "lot", "wafer", "'' AS gid", *ctx, *val_agg])
    return (f"SELECT {outer} FROM ({inner}) "
            f'GROUP BY {", ".join(["lot", "wafer", *mc])}')
