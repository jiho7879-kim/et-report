"""DuckDB → AppState 로딩. 분석 화면은 DB를 **읽기 전용**으로만 연다.

- 테이블 이름은 et_data 우선 (손코딩 시절 `select * from et_data` 그대로).
  없으면 fact, 그것도 없으면 가장 컬럼이 많은 테이블을 골라 쓴다.
- 컬럼 이름은 별칭 사전으로 자동 인식(compat.py) — 스키마가 달라도 읽는다.
- long(item_id/value) 테이블이면 읽으면서 PIVOT으로 wide화한다.
- 제외 포인트는 원본 DB에 쓰지 않고 사이드카 JSON에 둔다(exclusions.py).
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb
import polars as pl

from etreport.data import compat, exclusions
from etreport.model import wafers
from etreport.model.state import AppState

log = logging.getLogger(__name__)

#: 분석 wide 프레임에서 item이 **아닌** 컬럼. 나머지가 전부 item이다.
#: step/temp/site는 측정 조건 — 그룹 편집의 4단 필터와 배정 범위가 쓴다(§9.1).
RESERVED = ("key", "lot", "wafer", "gid", "step", "temp", "site")

# 분석 화면 외에 Excel/브라우저가 함께 도는 현장 PC를 위한 예측 가능한 상한.
DUCKDB_MEMORY_LIMIT = "4GB"


def readonly_config() -> dict:
    """읽기 전용 연결의 **공통 설정**.

    DuckDB는 같은 파일에 대해 설정이 다른 연결을 동시에 열지 못한다
    (`can't open a connection to same database file with a different
    configuration than existing connections`). 그래서 읽기 전용으로 여는
    자리는 전부 이 설정을 쓴다 — 새 코드가 `duckdb.connect(...)`를 직접
    부르면 그 순간 충돌한다.

    `temp_directory`는 큰 조회에서 디스크로 흘려보내기 위한 것이다. 없으면
    결과를 통째로 메모리에 만들다 `Out of Memory Error: Arrow buffer failed
    to allocate`로 죽는다.
    """
    from etreport.paths import appdata_dir
    tmp = appdata_dir() / "duckdb_tmp"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
    except OSError as e:                       # 권한이 없으면 기본값으로
        log.debug("DuckDB 임시 폴더를 만들지 못했습니다(%s) — 기본값 사용", e)
        return {"threads": 4, "memory_limit": DUCKDB_MEMORY_LIMIT}
    return {"threads": 4, "memory_limit": DUCKDB_MEMORY_LIMIT,
            "temp_directory": str(tmp)}


def open_readonly(db_path: str) -> duckdb.DuckDBPyConnection:
    """읽기 전용 연결 — 원본 파일을 절대 수정하지 않는다.

    **DB를 읽는 모든 자리가 이 함수를 쓴다.** 설정이 하나로 유지돼야
    같은 파일을 동시에 열 수 있다(readonly_config 참조).
    """
    try:
        return duckdb.connect(db_path, read_only=True,
                              config=readonly_config())
    except duckdb.Error as e:
        raise RuntimeError(explain_conn_error(e, db_path, write=False)) from e


@contextmanager
def readonly_query(db_path: str) -> Iterator[duckdb.DuckDBPyConnection]:
    """한 번 쓰고 닫는 읽기 전용 연결. **DB를 읽는 자리는 전부 이것을 쓴다.**

    DuckDB는 같은 파일·같은 설정의 연결을 **인스턴스 하나로 묶고**, 그 인스턴스의
    버퍼 캐시(최대 `DUCKDB_MEMORY_LIMIT`)는 마지막 연결이 닫힐 때에야 풀린다.
    예전에는 분석 화면이 연결(`state.store`)을 계속 열어 두어서, SQL 창에서 한 번
    무거운 조회를 돌리면 그 캐시가 분석 데이터·Excel 위에 그대로 남았고 그 뒤의
    모든 조회가 `Out of Memory Error`로 떨어졌다. 그래서 연결을 오래 쥐고 있는
    곳을 없앴다 — 열고, 읽고, 바로 닫는다.

    캐시를 비우려고 `SET memory_limit`을 잠깐 낮추는 방법은 쓰지 않는다. 상한은
    인스턴스 전역이라 다른 스레드(백그라운드 작업)가 같은 파일을 읽는 중이면
    그 조회가 OOM으로 떨어진다.
    """
    con = open_readonly(db_path)
    try:
        yield con
    finally:
        con.close()


def explain_conn_error(e: Exception, db_path: str, write: bool) -> str:
    """DuckDB 연결 오류를 사람이 읽을 수 있는 안내로 바꾼다.

    현장에서 가장 많이 보는 것이 "설정이 다른 연결" 오류인데, 원문만으로는
    무엇을 해야 하는지 알 수 없다. 원인은 하나다 — **같은 파일을 쓰기와 읽기
    전용으로 동시에 열었다**(적재 중에 분석 화면이 그 DB를 붙잡고 있거나,
    그 반대).
    """
    msg = str(e)
    if "different configuration" not in msg and "already open" not in msg:
        return f"DB를 열지 못했습니다: {msg}"
    what = ("적재(쓰기)" if write else "분석(읽기 전용)")
    other = ("분석 화면" if write else "데이터 화면의 적재")
    return (f"{Path(db_path).name}을(를) {what}로 열 수 없습니다 — "
            f"같은 파일을 {other}이(가) 이미 다른 방식으로 열고 있습니다.\n\n"
            f"[분석] 화면에서 다른 DB를 고르거나 [적용]을 다시 누른 뒤, "
            f"또는 앱을 재시작한 뒤 다시 시도하세요.\n\n원문: {msg}")


def item_columns(df: pl.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in RESERVED]


def apply_absolute(df: pl.DataFrame, rf) -> tuple[pl.DataFrame, int]:
    """ABSOLUTE 항목에 절대값을 **다시** 적용한다. 적용한 item 수를 함께 반환.

    계획서 §10.2 — 추출 시점에만 적용하면, 이미 음수로 적재된 DB는 리포메터를
    고쳐도 그대로다("ABSOLUTE가 안 먹는 것처럼 보이는" 문제). 그래서 분석
    로딩·[적용] 시점에도 건다. 절대값은 멱등(`|x|` 두 번 = `|x|`)이라 몇 번을
    걸어도 값이 같다.

    **스케일은 멱등이 아니므로 여기서 절대 재적용하지 않는다** — 다시 곱하면
    적재된 값이 배율만큼 어긋난다.
    """
    rules = getattr(rf, "rules", None) or []
    cols = list(dict.fromkeys(          # 같은 alias가 두 번 있어도 한 번만
        r.alias for r in rules
        if r.absolute and r.alias in df.columns and r.alias not in RESERVED
        and df.schema[r.alias].is_numeric()))
    if not cols:
        return df, 0
    return df.with_columns([pl.col(c).abs() for c in cols]), len(cols)


def apply_manual_groups(df: pl.DataFrame,
                        groups: dict[tuple, str]) -> pl.DataFrame:
    """그룹 편집에서 손으로 배정한 것을 프레임에 반영한다.

    키는 `(lot, wafer, step, temp, site)`이고 **None은 '조건 무관'**이다.
    필터를 좁혀 배정했다면 그 범위의 행에만 gid가 붙는다(§9.1) — 같은 wafer라도
    step·온도·site가 다르면 다른 측정점이기 때문이다. 값이 빈 문자열이면
    '미배정'을 명시한 것이므로 실험 조건이 붙여 둔 gid도 지운다.

    조건 비교는 문자열로 한다 — 온도 25.0을 콤보에서 고를 때와 프레임에 든
    값이 같은 표기가 되도록.

    lot·wafer는 **정규화한 표기로 비교한다**(`model/wafers`) — 붙여넣기로 적어
    둔 `1`과 DB의 `W01`이 같은 wafer로 붙어야 한다.
    """
    if df is None or not groups or "gid" not in df.columns:
        return df
    lot_k = wafers.lot_key_expr("lot")
    waf_k = wafers.wafer_key_expr("wafer")
    expr = pl.col("gid")
    for key, gid in groups.items():          # 나중에 배정한 것이 이긴다
        lot, wafer, *ctx = key
        cond = (lot_k == wafers.norm_lot(lot)) & (waf_k == wafers.norm_wafer(wafer))
        for name, val in zip(compat.CTX_ROLES, ctx):
            if val is not None and name in df.columns:
                cond = cond & (pl.col(name).cast(pl.Utf8) == val)
        expr = pl.when(cond).then(pl.lit(gid)).otherwise(expr)
    return df.with_columns(expr.alias("gid"))


def wafer_index_from_frame(df: pl.DataFrame) -> pl.DataFrame:
    """이미 읽어 둔 분석 프레임 → (lot, wafer, step, temp, site, n)."""
    cols = ["lot", "wafer"]
    have = [c for c in compat.CTX_ROLES if c in df.columns]
    idx = (df.group_by([*cols, *have]).len()
           .rename({"len": "n"})
           .with_columns([pl.col(c).cast(pl.Utf8) for c in have]))
    missing = [c for c in compat.CTX_ROLES if c not in have]
    if missing:
        idx = idx.with_columns([pl.lit(None, dtype=pl.Utf8).alias(c)
                                for c in missing])
    return idx.select(["lot", "wafer", *compat.CTX_ROLES, "n"])


def wafer_index_from_db(db_path: str,
                        lots: list[str] | None = None) -> pl.DataFrame:
    """DB를 읽기 전용으로 열어 조회 — **[적용] 없이도 동작해야 한다**(§9.1).

    item 컬럼을 만지지 않으므로 큰 DB에서도 가볍다. `lots`를 주면 도크에서 고른
    lot으로 좁힌다 — 그룹 편집이 분석 대상과 같은 범위를 보게 하기 위해서다.
    """
    with readonly_query(db_path) as con:
        tbl = compat.pick_table(con)
        if tbl is None:
            return wafer_index_empty()
        prof = compat.profile(con, tbl)
        idx = con.execute(compat.wafer_index_sql(prof, lots)).pl()
    return idx.with_columns([pl.col(c).cast(pl.Utf8) for c in compat.CTX_ROLES])


def lot_index(db_path: str) -> pl.DataFrame:
    """DB에 들어 있는 lot 목록 — `(lot, wafers)`. 도크 lot 리스트가 쓴다.

    DB를 **고르는 즉시**(=[적용] 전에) 도는 조회다. 그래서 item도 die 좌표도 보지
    않고 lot·wafer만 센다. 읽기 전용 연결은 반드시 `readonly_query()`를 거친다 —
    설정이 다른 연결을 같은 파일에 하나라도 더 열면 그 순간 DuckDB가 막는다.
    """
    empty = pl.DataFrame(schema={"lot": pl.Utf8, "wafers": pl.Int64})
    if not Path(db_path).exists():
        return empty
    with readonly_query(db_path) as con:
        tbl = compat.pick_table(con)
        if tbl is None:
            return empty
        sql = compat.lot_index_sql(compat.profile(con, tbl))
        if not sql:                      # lot 컬럼을 못 찾은 스키마
            return empty
        idx = con.execute(sql).pl()
    return idx.with_columns(pl.col("lot").cast(pl.Utf8))


def wafer_index_empty() -> pl.DataFrame:
    return pl.DataFrame(schema={"lot": pl.Utf8, "wafer": pl.Utf8,
                                "step": pl.Utf8, "temp": pl.Utf8,
                                "site": pl.Utf8, "n": pl.Int64})


def close_store(state: AppState) -> None:
    """남아 있는 읽기 전용 연결을 닫는다.

    `load_state`는 이제 연결을 쥐고 있지 않으므로 보통은 할 일이 없다. 예전
    코드·테스트가 `state.store`에 연결을 넣어 둔 경우를 위한 안전장치다 —
    열린 연결이 하나라도 남으면 DuckDB 캐시와 파일 잠금이 함께 남는다.
    """
    con = getattr(state, "store", None)
    if con is None:
        return
    try:
        con.close()
    except Exception as e:                          # noqa: BLE001 — 이미 닫혔을 수 있다
        log.debug("이전 DB 연결 닫기 실패(무시): %s", e)
    state.store = None


def load_state(state: AppState, db_path: str, table: str | None = None,
               lots: list[str] | None = None) -> str:
    """DB를 열어 state.data를 채우고 상태 요약을 반환.

    `lots`가 있으면 그 lot만 읽는다(§9.2). 읽는 양 자체가 줄어드는 것이 요점이라
    프레임을 다 만든 뒤 거르지 않고 **SQL에서** 좁힌다. 빈 리스트·None은 '전부'다.
    """
    if not Path(db_path).exists():
        raise FileNotFoundError(f"파일이 없습니다: {db_path}")

    close_store(state)                              # 이전 연결부터 정리
    # 연결은 읽는 동안만 연다(readonly_query 참조). 열어 두면 조회 캐시가
    # 화면 수명만큼 남아 이후 모든 DuckDB 접근이 OOM이 된다.
    with readonly_query(db_path) as con:
        tbl = compat.pick_table(con, table)
        if tbl is None:
            state.data = None
            state.db_label = Path(db_path).name
            return "테이블이 없습니다 — [데이터]에서 먼저 추출·적재하세요"
        prof = compat.profile(con, tbl)
        log.info("조회 테이블 %s", prof.describe())
        df = con.execute(compat.select_sql(prof, lots=lots)).pl()
    if prof.is_long:
        df = _normalize_pivoted(df, prof)
    df, n_abs = apply_absolute(df, state.rf)     # 음수로 적재된 기존 DB도 교정

    state.table = tbl
    state.profile = prof
    state.data = df
    state.db_path = db_path
    state.excl_points = exclusions.load(db_path)
    state.excluded = set(state.excl_points)
    state.undo_stack.clear()
    state.db_label = Path(db_path).name

    if state.split is not None and state.factors:
        assign = state.split.assignment(state.factors)
        state.groups = state.split.styles_for(state.factors)
        state.data = state.data.with_columns(pl.Series(
            "gid", wafers.map_gids(df["lot"], df["wafer"], assign)))
    # 손으로 배정한 그룹은 [적용]으로 DB를 다시 읽어도 살아남는다. 실험 조건
    # 배정보다 뒤에 걸어 사용자가 직접 고른 쪽이 이기게 한다.
    state.data = apply_manual_groups(state.data, state.manual_groups)
    state.data = reattach_sources(state.data, state)

    items = item_columns(state.data)
    n_lot = state.data["lot"].n_unique() if "lot" in state.data.columns else 0
    # lot을 골라 읽었으면 '3/12'로 적는다 — 화면에 없는 lot이 DB에는 있다는 사실을
    # 요약 한 줄이 말해 주지 않으면, 빠진 lot을 데이터가 없는 것으로 오해한다.
    total = len(state.lots_all)
    lot_txt = f"{n_lot}/{total}" if lots and total > n_lot else str(n_lot)
    return (f"{tbl} · {state.data.height:,} 포인트 · lot {lot_txt} "
            f"· item {len(items)} · 제외 {len(state.excluded)}"
            + (f" · 절대값 {n_abs}" if n_abs else ""))


def reattach_sources(df: pl.DataFrame, state: AppState) -> pl.DataFrame:
    """붙여 둔 fab tracking·inline 계측 열을 다시 붙인다(§1·§2).

    [적용]은 DuckDB에서 프레임을 새로 만든다. 다시 붙이지 않으면 이름만
    `state.track_columns`·`met_columns`에 남고 **컬럼은 사라져서**, 축 후보에는
    보이는데 어디에도 반영되지 않는 상태가 된다("분석에 활용이 안 먹는다").
    붙일 원본이 없으면(조회 전) 아무 일도 하지 않는다.
    """
    if df is None:
        return df
    if state.track_frame is not None:
        from etreport.data import fabtracking as ft
        df, _ = ft.attach(df, state.track_frame)
    if state.met_frame is not None:
        from etreport.data import metrology as mt
        df, _ = mt.attach(df, state.met_frame, state.met_level)
    return df


def _normalize_pivoted(df: pl.DataFrame, prof: compat.TableProfile) -> pl.DataFrame:
    """PIVOT 결과에 key/lot/wafer/gid + step/temp/site 표준 컬럼을 붙인다."""
    ren = {}
    if (lot := prof.roles.get("lot")) and lot in df.columns:
        ren[lot] = "lot"
    if (waf := prof.roles.get("wafer")) and waf in df.columns:
        ren[waf] = "wafer"
    for role in compat.CTX_ROLES:                 # step·temp·site는 이름만 맞춘다
        if (col := prof.roles.get(role)) and col in df.columns:
            ren[col] = role
    df = df.rename(ren)

    others = {prof.roles.get(r) for r in ("x", "y", "seq", "time")}
    keyparts = [c for c in df.columns
                if c in ("lot", "wafer", *compat.CTX_ROLES) or c in others]
    if keyparts:
        key = pl.concat_str([pl.col(c).cast(pl.Utf8).fill_null("_")
                             for c in keyparts], separator="|").hash()
    else:
        key = pl.int_range(pl.len())
    df = df.with_columns(key.cast(pl.Utf8).alias("key"),
                         pl.lit("").alias("gid"))
    # die 좌표·seq·시각은 분석에 쓰이지 않으므로 떨군다 — wide 경로와 동일하게
    drop = [c for c in others if c and c in df.columns]
    if drop:
        df = df.drop(drop)
    for col, default in (("lot", "(lot?)"), ("wafer", "(wafer?)")):
        if col not in df.columns:
            df = df.with_columns(pl.lit(default).alias(col))
    for role in compat.CTX_ROLES:
        if role not in df.columns:
            df = df.with_columns(pl.lit(None).alias(role))
    front = list(RESERVED)
    return df.select(front + [c for c in df.columns if c not in front])


def sync_exclusion(state: AppState, key: str, exclude: bool,
                   reason: str = "탐색 화면에서 제외") -> None:
    """제외/복원은 사이드카에만 기록 — 원본 DB는 건드리지 않는다."""
    if not getattr(state, "db_path", ""):
        return
    if exclude:
        exclusions.add(state.db_path, state.excl_points, key, reason)
    else:
        exclusions.remove(state.db_path, state.excl_points, key)


def exclusion_frame(state: AppState, include_filtered: bool = False
                    ) -> pl.DataFrame:
    """PPT 제외 이력 슬라이드용.

    `include_filtered=True`면 이상치 필터가 걸러 낸 점도 함께 싣는다. 기본값이
    False인 이유는 이 함수가 "**사람이** 뺀 점"을 뜻해 온 자리이기 때문이다 —
    세는 곳(화면의 '제외 N점')이 갑자기 수백으로 뛰면 안 된다. PPT는 무엇이
    빠졌는지 전부 남겨야 하므로 켜서 부른다.
    """
    schema = {"key_hash": pl.Utf8, "reason": pl.Utf8, "created_at": pl.Utf8}

    def _frame(pts: dict) -> pl.DataFrame:
        return pl.DataFrame({
            "key_hash": list(pts),
            "reason": [v.get("reason", "") for v in pts.values()],
            "created_at": [v.get("at", "") for v in pts.values()],
        }, schema=schema)

    out = _frame(getattr(state, "excl_points", {}) or {})
    if include_filtered:
        out = pl.concat([out, _frame(getattr(state, "filtered", {}) or {})])
    return out
