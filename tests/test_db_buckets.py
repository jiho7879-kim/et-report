"""버킷 수는 **작업 단위**일 뿐 — 저장 결과를 바꾸면 안 된다.

적재 속도를 위해 버킷 수를 데이터 모양에 맞춰 정하도록 바꿨다(plan_buckets).
그래서 여기서 못 박아야 하는 것은 속도가 아니라 **불변식**이다:

  · 예전에 512개 버킷으로 적재해 둔 DB에 이어 적재해도 결과가 같다
  · 버킷 수가 달라도 key_hash·컬럼·행 수·값이 모두 같다
  · 조회 전용으로만 쓰던 DB(예전 스키마, `fact` 테이블)도 그대로 읽힌다
"""
from __future__ import annotations

import duckdb
import polars as pl
import pytest

from etreport.data import compat, db, loader
from etreport.data.reformatter import apply as rf_apply
from tests.factory import make_long, make_reformatter


@pytest.fixture
def parquet(tmp_path):
    rf = make_reformatter(n_real=20, n_addp=5, seed=3)
    src = make_long([r.itemid for r in rf.reals()],
                    lots=1, wafers=4, chips=4, seed=3, null_rate=0.05)
    p = tmp_path / "raw_20260801_20260801_a.parquet"
    rf_apply(rf, src).write_parquet(p)
    return p


def snapshot(db_path) -> pl.DataFrame:
    """비교용 — et_data 전체를 정렬해서 뽑는다."""
    con = duckdb.connect(str(db_path), read_only=True)
    df = con.execute("select * from et_data order by key_hash").pl()
    con.close()
    return df.select(sorted(df.columns))


# ── plan_buckets 자체 ────────────────────────────────────────
@pytest.mark.parametrize("keys,items,expect", [
    (200, 1040, 1),          # 사용자 실측 하루치 → 한 번에
    (1400, 1040, 1),         # 일주일치도 한 번에 (약 146만 셀)
    (20_000, 1040, 6),       # 3개월치쯤 되면 쪼갠다
    (10_000_000, 1040, 512), # 아무리 커도 상한은 N_BUCKETS
    (0, 1040, 1),            # 빈 입력
    (5, 0, 1),
])
def test_plan_buckets(keys, items, expect):
    assert db.plan_buckets(keys, items) == expect


def test_plan_buckets_bounds_pivot_size():
    """어떤 입력이든 버킷 하나의 셀 수가 목표를 크게 넘지 않아야 한다."""
    for keys in (1, 137, 5_000, 200_000):
        for items in (1, 40, 1_000, 5_000):
            n = db.plan_buckets(keys, items)
            per_bucket = keys / n * items
            assert per_bucket <= db.TARGET_CELLS or n == db.N_BUCKETS


# ── 불변식 ───────────────────────────────────────────────────
def test_bucket_count_does_not_change_stored_data(parquet, tmp_path,
                                                  monkeypatch):
    """버킷 1개로 적재한 DB와 32개로 적재한 DB의 내용이 완전히 같아야 한다."""
    monkeypatch.setattr(db, "TARGET_CELLS", 10 ** 12)      # → 1개
    a = tmp_path / "one.duckdb"
    n_a = db.pivot_and_load(db.Store(a), [parquet])

    monkeypatch.setattr(db, "TARGET_CELLS", 10)            # → 잘게
    b = tmp_path / "many.duckdb"
    n_b = db.pivot_and_load(db.Store(b), [parquet])

    assert n_a == n_b
    sa, sb = snapshot(a), snapshot(b)
    assert sa.columns == sb.columns
    assert sa.height == sb.height
    assert sa.equals(sb)


def test_append_to_db_loaded_with_other_bucket_count(parquet, tmp_path,
                                                     monkeypatch):
    """예전(512개)으로 적재해 둔 DB에 이어 적재 — 중복이 생기면 안 된다."""
    dbp = tmp_path / "legacy_buckets.duckdb"
    monkeypatch.setattr(db, "TARGET_CELLS", 10)            # 예전처럼 잘게
    first = db.pivot_and_load(db.Store(dbp), [parquet])
    before = snapshot(dbp)

    monkeypatch.setattr(db, "TARGET_CELLS", 10 ** 12)      # 지금처럼 한 번에
    again = db.pivot_and_load(db.Store(dbp), [parquet])

    after = snapshot(dbp)
    assert first > 0 and again == 0          # 전부 중복 → 새로 들어간 행 없음
    assert after.height == before.height
    assert after.equals(before)


def test_new_points_still_append(parquet, tmp_path, monkeypatch):
    """이어 적재 자체는 되어야 한다 — 새 wafer는 늘어난다."""
    rf = make_reformatter(n_real=20, n_addp=5, seed=3)
    dbp = tmp_path / "grow.duckdb"
    n1 = db.pivot_and_load(db.Store(dbp), [parquet])

    src2 = make_long([r.itemid for r in rf.reals()], lots=1, wafers=4, chips=4,
                     seed=3, null_rate=0.05, start=__import__(
                         "datetime").date(2026, 8, 2))
    p2 = tmp_path / "raw_20260802_20260802_b.parquet"
    rf_apply(rf, src2).write_parquet(p2)
    n2 = db.pivot_and_load(db.Store(dbp), [p2])

    assert n2 > 0
    con = duckdb.connect(str(dbp), read_only=True)
    assert con.execute("select count(*) from et_data").fetchone()[0] == n1 + n2
    con.close()


def test_views_and_log_survive_reload(parquet, tmp_path):
    dbp = tmp_path / "views.duckdb"
    db.pivot_and_load(db.Store(dbp), [parquet])
    con = duckdb.connect(str(dbp), read_only=True)
    assert con.execute("select count(*) from v_latest").fetchone()[0] > 0
    files = [r[0] for r in con.execute(
        "select file_name from load_log where note='file'").fetchall()]
    assert files == [parquet.name]
    con.close()


# ── 예전 DB 호환 (조회 전용) ─────────────────────────────────
def test_legacy_fact_table_is_read_and_appended(tmp_path, parquet, appdata):
    """`fact` 테이블만 있는 예전 DB — 읽기도 되고 이어 적재도 된다."""
    from etreport.model.state import AppState

    dbp = tmp_path / "old.duckdb"
    src = pl.read_parquet(parquet)
    wide = (src.with_columns(key_hash=db.key_hash_expr())
            .pivot(on="item_id", index=[*db.KEY9, "line_id", "key_hash"],
                   values="value", aggregate_function="first"))
    con = duckdb.connect(str(dbp))
    con.register("w", wide.to_arrow())
    con.execute("create table fact as select * from w")     # 예전 이름
    con.close()

    store = db.Store(dbp)
    assert store.table_name() == "fact"
    assert db.pivot_and_load(store, [parquet]) == 0          # 이미 있는 포인트
    store.con.close()

    st = AppState()
    summary = loader.load_state(st, str(dbp))
    assert "fact" in summary
    assert st.data.height == wide.height


def test_query_only_db_is_never_written(parquet, tmp_path, appdata):
    """분석 화면이 연 DB는 파일이 바뀌지 않아야 한다(조회 전용 원칙)."""
    from etreport.model.state import AppState

    dbp = tmp_path / "readonly.duckdb"
    db.pivot_and_load(db.Store(dbp), [parquet])
    before = (dbp.stat().st_size, dbp.stat().st_mtime_ns)

    st = AppState()
    loader.load_state(st, str(dbp))
    key = st.data["key"][0]
    loader.sync_exclusion(st, key, True, reason="조회 전용 확인")
    loader.close_store(st)

    assert (dbp.stat().st_size, dbp.stat().st_mtime_ns) == before
    con = duckdb.connect(str(dbp), read_only=True)
    assert "exclusions" not in compat.list_tables(con)       # 제외는 사이드카에만
    con.close()
