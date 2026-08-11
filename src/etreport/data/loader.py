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

RESERVED = ("key", "lot", "wafer", "gid")


def open_readonly(db_path: str) -> duckdb.DuckDBPyConnection:
    """읽기 전용 연결 — 원본 파일을 절대 수정하지 않는다."""
    return duckdb.connect(db_path, read_only=True)


def item_columns(df: pl.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in RESERVED]


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

    items = item_columns(state.data)
    n_lot = state.data["lot"].n_unique() if "lot" in state.data.columns else 0
    return (f"{tbl} · {state.data.height:,} 포인트 · lot {n_lot} "
            f"· item {len(items)} · 제외 {len(state.excluded)}")


def _normalize_pivoted(df: pl.DataFrame, prof: compat.TableProfile) -> pl.DataFrame:
    """PIVOT 결과에 key/lot/wafer/gid 표준 컬럼을 붙인다."""
    ren = {}
    if (lot := prof.roles.get("lot")) and lot in df.columns:
        ren[lot] = "lot"
    if (waf := prof.roles.get("wafer")) and waf in df.columns:
        ren[waf] = "wafer"
    df = df.rename(ren)

    others = {prof.roles.get(r) for r in
              ("x", "y", "temp", "step", "seq", "site", "time")}
    keyparts = [c for c in df.columns if c in ("lot", "wafer") or c in others]
    if keyparts:
        key = pl.concat_str([pl.col(c).cast(pl.Utf8).fill_null("_")
                             for c in keyparts], separator="|").hash()
    else:
        key = pl.int_range(pl.len())
    df = df.with_columns(key.cast(pl.Utf8).alias("key"),
                         pl.lit("").alias("gid"))
    # 키 컬럼은 분석에 쓰이지 않으므로 떨군다 — wide 경로와 동일하게
    drop = [c for c in others if c and c in df.columns]
    if drop:
        df = df.drop(drop)
    if "lot" not in df.columns:
        df = df.with_columns(pl.lit("(lot?)").alias("lot"))
    if "wafer" not in df.columns:
        df = df.with_columns(pl.lit("(wafer?)").alias("wafer"))
    front = ["key", "lot", "wafer", "gid"]
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
