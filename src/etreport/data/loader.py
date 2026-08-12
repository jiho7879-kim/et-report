"""DuckDB → AppState 로딩. 분석 화면은 DB를 **읽기 전용**으로만 연다.

- 테이블 이름은 et_data 우선 (손코딩 시절 `select * from et_data` 그대로).
  없으면 fact, 그것도 없으면 가장 컬럼이 많은 테이블을 골라 쓴다.
- 컬럼 이름은 별칭 사전으로 자동 인식(compat.py) — 스키마가 달라도 읽는다.
- long(item_id/value) 테이블이면 읽으면서 PIVOT으로 wide화한다.
- 제외 포인트는 원본 DB에 쓰지 않고 사이드카 JSON에 둔다(exclusions.py).
"""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import polars as pl

from etreport.data import compat, exclusions
from etreport.model.state import AppState

log = logging.getLogger(__name__)

#: 분석 wide 프레임에서 item이 **아닌** 컬럼. 나머지가 전부 item이다.
#: step/temp/site는 측정 조건 — 그룹 편집의 4단 필터와 배정 범위가 쓴다(§9.1).
RESERVED = ("key", "lot", "wafer", "gid", "step", "temp", "site")


def open_readonly(db_path: str) -> duckdb.DuckDBPyConnection:
    """읽기 전용 연결 — 원본 파일을 절대 수정하지 않는다."""
    return duckdb.connect(db_path, read_only=True)


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
    """
    if df is None or not groups or "gid" not in df.columns:
        return df
    expr = pl.col("gid")
    for key, gid in groups.items():          # 나중에 배정한 것이 이긴다
        lot, wafer, *ctx = key
        cond = (pl.col("lot") == lot) & (pl.col("wafer") == wafer)
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


def wafer_index_from_db(db_path: str) -> pl.DataFrame:
    """DB를 읽기 전용으로 열어 조회 — **[적용] 없이도 동작해야 한다**(§9.1).

    item 컬럼을 만지지 않으므로 큰 DB에서도 가볍다.
    """
    con = open_readonly(db_path)
    try:
        tbl = compat.pick_table(con)
        if tbl is None:
            return wafer_index_empty()
        prof = compat.profile(con, tbl)
        idx = con.execute(compat.wafer_index_sql(prof)).pl()
    finally:
        con.close()
    return idx.with_columns([pl.col(c).cast(pl.Utf8) for c in compat.CTX_ROLES])


def wafer_index_empty() -> pl.DataFrame:
    return pl.DataFrame(schema={"lot": pl.Utf8, "wafer": pl.Utf8,
                                "step": pl.Utf8, "temp": pl.Utf8,
                                "site": pl.Utf8, "n": pl.Int64})


def close_store(state: AppState) -> None:
    """이전에 열어 둔 읽기 전용 연결을 닫는다.

    [적용]을 누를 때마다 새 연결을 만들기 때문에, 닫지 않으면 세션이 길어질수록
    연결과 파일 핸들이 계속 쌓인다(Windows에서는 DB 파일도 계속 잡혀 있다).
    """
    con = getattr(state, "store", None)
    if con is None:
        return
    try:
        con.close()
    except Exception as e:                          # noqa: BLE001 — 이미 닫혔을 수 있다
        log.debug("이전 DB 연결 닫기 실패(무시): %s", e)
    state.store = None


def load_state(state: AppState, db_path: str, table: str | None = None) -> str:
    """DB를 열어 state.data를 채우고 상태 요약을 반환."""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"파일이 없습니다: {db_path}")

    close_store(state)                              # 이전 연결부터 정리
    con = open_readonly(db_path)
    tbl = compat.pick_table(con, table)
    if tbl is None:
        state.store, state.data = con, None
        state.db_label = Path(db_path).name
        return "테이블이 없습니다 — [데이터]에서 먼저 추출·적재하세요"

    prof = compat.profile(con, tbl)
    log.info("조회 테이블 %s", prof.describe())
    df = con.execute(compat.select_sql(prof)).pl()
    if prof.is_long:
        df = _normalize_pivoted(df, prof)
    df, n_abs = apply_absolute(df, state.rf)     # 음수로 적재된 기존 DB도 교정

    state.store = con
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
            "gid", [assign.get((lo, wa), "") for lo, wa
                    in zip(df["lot"], df["wafer"])]))
    # 손으로 배정한 그룹은 [적용]으로 DB를 다시 읽어도 살아남는다. 실험 조건
    # 배정보다 뒤에 걸어 사용자가 직접 고른 쪽이 이기게 한다.
    state.data = apply_manual_groups(state.data, state.manual_groups)

    items = item_columns(state.data)
    n_lot = state.data["lot"].n_unique() if "lot" in state.data.columns else 0
    return (f"{tbl} · {state.data.height:,} 포인트 · lot {n_lot} "
            f"· item {len(items)} · 제외 {len(state.excluded)}"
            + (f" · 절대값 {n_abs}" if n_abs else ""))


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


def exclusion_frame(state: AppState) -> pl.DataFrame:
    """PPT 제외 이력 슬라이드용."""
    pts = getattr(state, "excl_points", {}) or {}
    if not pts:
        return pl.DataFrame({"key_hash": [], "reason": [], "created_at": []})
    return pl.DataFrame({
        "key_hash": list(pts),
        "reason": [v.get("reason", "") for v in pts.values()],
        "created_at": [v.get("at", "") for v in pts.values()],
    })
