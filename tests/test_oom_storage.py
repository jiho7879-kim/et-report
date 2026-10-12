"""DuckDB OOM 회귀(2026-10) — item 수천 개 · 1년치에서도 적재·[적용]이 버틴다.

원인 두 가지를 각각 고정한다.
- **적재**: DuckDB checkpoint 메모리는 행 수가 아니라 **열 수**에 비례한다.
  item 5,000개짜리 한 테이블은 이어 붙이기 한 번에 4GB 상한을 넘었다 →
  item을 `ITEMS_PER_TABLE`개씩 나눈 테이블 + `et_data` 뷰.
- **읽기**: [적용]이 item 전부를 읽고 병합 `arg_max`를 item마다 걸었다 →
  템플릿 item만 읽고(`load_state(items=…)`), 나머지는 `ensure_items`로 열 단위.
"""
from __future__ import annotations

from datetime import datetime

import duckdb
import polars as pl
import pytest

from etreport.data import compat, db, loader
from etreport.model.specs import PlotSpec
from etreport.model.state import AppState


def _long(n_items: int, n_keys: int = 4, seq_split: bool = False,
          day: int = 1, first_item: int = 0, first_key: int = 0) -> pl.DataFrame:
    """키 n_keys개 × item n_items개 long. seq_split이면 뒤 절반 item은 seq 2에.

    값은 `키 번호 × 10,000 + item 번호`라 어느 칸이 어디서 왔는지 바로 안다.
    """
    rows = []
    for k in range(first_key, first_key + n_keys):
        for i in range(first_item, first_item + n_items):
            seq = 2 if seq_split and i >= n_items // 2 else 1
            rows.append({
                "root_lot_id": f"L{k % 2}", "wafer_id": f"W{k:02d}",
                "chip_x_pos": 0, "chip_y_pos": 0, "temperature": 25.0,
                "step_id": "S1", "step_seq": str(seq), "total_site_cnt": 1,
                "tkout_time": datetime(2026, 1, day, seq), "line_id": "LN",
                "item_id": f"it{i:05d}", "value": float(k * 10_000 + i)})
    return pl.DataFrame(rows)


def _load(dbp, frame, tmp_path, name="a.parquet"):
    p = tmp_path / name
    frame.write_parquet(p)
    with db.Store(dbp) as s:
        return db.pivot_and_load(s, [p]), s.filled


def _tables(dbp) -> dict[str, str]:
    con = duckdb.connect(str(dbp), read_only=True)
    try:
        return dict(con.execute(
            "SELECT table_name, table_type FROM information_schema.tables"
        ).fetchall())
    finally:
        con.close()


# ── 적재: 열 수에 천장 ────────────────────────────────────────────
def test_items_are_split_into_capped_tables_behind_et_data_view(tmp_path, appdata):
    """item 1,200개 → 500·500·200 세 테이블, `et_data`는 뷰로 전부 돌려준다."""
    dbp = tmp_path / "et.duckdb"
    assert _load(dbp, _long(1200), tmp_path) == (4, 0)
    t = _tables(dbp)
    assert t["et_data"] == "VIEW" and t[db.KEYS_TABLE] == "BASE TABLE"
    items = sorted(n for n in t if n.startswith(db.ITEMS_PREFIX))
    assert len(items) == 3
    con = duckdb.connect(str(dbp), read_only=True)
    try:
        widths = [con.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_name=?",
            [n]).fetchone()[0] - 1 for n in items]       # key_hash 빼고
        assert widths == [500, 500, 200]
        got = con.execute('SELECT wafer_id, "it00000", "it00777", "it01199" '
                          "FROM et_data ORDER BY wafer_id").fetchall()
    finally:
        con.close()
    assert got[3] == ("W03", 30_000.0, 30_777.0, 31_199.0)


def test_reload_is_idempotent_and_new_items_fill_the_gaps(tmp_path, appdata):
    """같은 파일 재적재 = (0, 0). item을 더하면 행은 그대로 칸만 채운다(§6)."""
    dbp = tmp_path / "et.duckdb"
    _load(dbp, _long(600), tmp_path)
    assert _load(dbp, _long(600), tmp_path, "b.parquet") == (0, 0)
    # 키 4개는 그대로, item 600 → 900 (새 300개는 둘째 테이블의 빈자리 + 셋째)
    assert _load(dbp, _long(1100), tmp_path, "c.parquet") == (0, 4)
    con = duckdb.connect(str(dbp), read_only=True)
    try:
        n, v = con.execute('SELECT count(*), max("it01099") FROM et_data').fetchone()
    finally:
        con.close()
    assert (n, v) == (4, 31_099.0)


def test_rows_stay_aligned_across_uneven_loads(tmp_path, appdata):
    """★ 키·item 구성이 매번 다른 적재를 거듭해도 모든 칸이 제 행에 붙는다.

    et_data는 item 테이블을 **행 번호로** 붙인다(POSITIONAL JOIN). 순서가 하나라도
    어긋나면 값이 남의 측정에 조용히 들어가므로, 칸 하나하나를 정답과 대조한다.
    """
    dbp = tmp_path / "et.duckdb"
    _load(dbp, _long(600, first_key=0), tmp_path, "a.parquet")      # 키 0~3, item 0~599
    _load(dbp, _long(900, first_key=4, first_item=300),             # 키 4~7, item 300~1199
          tmp_path, "b.parquet")                                     # (셋째 테이블이 새로 열림)
    _load(dbp, _long(1200, first_key=2), tmp_path, "c.parquet")     # 키 2~5, 빈 칸 채움
    con = duckdb.connect(str(dbp), read_only=True)
    try:
        got = con.execute("SELECT * FROM et_data").pl()
    finally:
        con.close()
    assert got.height == 8
    for row in got.iter_rows(named=True):
        k = int(row["wafer_id"][1:])
        for i in range(1200):
            have = (k in (2, 3, 4, 5) or (k < 2 and i < 600)
                    or (k > 5 and i >= 300))
            want = float(k * 10_000 + i) if have else None
            assert row[f"it{i:05d}"] == want, (k, i)


def test_wide_append_survives_a_tight_memory_cap(tmp_path, appdata, monkeypatch):
    """★ item 3,000개를 1GB 상한에서 여러 번 이어 적재해도 checkpoint가 죽지 않는다.

    한 테이블이던 시절에는 이 모양이 `Failed to create checkpoint`로 떨어졌다.
    """
    monkeypatch.setattr(loader, "duckdb_memory_limit", lambda total=None: "1GB")
    dbp = tmp_path / "big.duckdb"
    total = 0
    for d in range(1, 4):                    # 날짜마다 다른 키로 이어 붙인다
        n, _ = _load(dbp, _long(3000, n_keys=8, day=d), tmp_path, f"d{d}.parquet")
        total += n
    with db.Store(dbp) as s:
        s.con.execute("CHECKPOINT")
    assert total == 24


def test_legacy_single_table_db_still_appends(tmp_path, appdata):
    """예전 형식(et_data가 테이블) DB는 예전 경로 그대로 이어 적재된다."""
    dbp = tmp_path / "old.duckdb"
    wide = (_long(5).with_columns(key_hash=db.key_hash_expr())
            .pivot(on="item_id", index=[*db.KEY9, "line_id", "key_hash"],
                   values="value", aggregate_function="first"))
    con = duckdb.connect(str(dbp))
    con.register("w", wide.to_arrow())
    con.execute("CREATE TABLE et_data AS SELECT * FROM w")
    con.close()
    assert _load(dbp, _long(5, n_keys=6), tmp_path) == (2, 0)
    assert _tables(dbp)["et_data"] == "BASE TABLE"


# ── 읽기: 필요한 item만 ──────────────────────────────────────────
def test_select_sql_reads_only_requested_items():
    """wide는 열을, long은 item_id를 좁힌다. None이면 예전과 같은 SQL."""
    p = compat.TableProfile("et_data", {}, roles={"lot": "lot", "wafer": "w"},
                            items=["A", "B", "C"])
    assert compat.select_sql(p) == compat.select_sql(p, items=None)
    sql = compat.select_sql(p, items=["C", "Z"])
    assert '"C"' in sql and '"A"' not in sql and '"B"' not in sql
    lp = compat.TableProfile("t", {}, roles={"lot": "lot", "item": "item_id",
                                            "value": "value"}, is_long=True)
    assert "\"item_id\" IN ('A')" in compat.select_sql(lp, items=["A"])


def _big_db(tmp_path, n_items=400):
    dbp = tmp_path / "et.duckdb"
    _load(dbp, _long(n_items, seq_split=True), tmp_path)
    return str(dbp)


def test_load_state_reads_only_the_named_items(tmp_path, appdata):
    """item이 많은 DB는 고른 것만 올리고, 무엇이 있는지는 `db_items`가 안다."""
    path = _big_db(tmp_path)
    st = AppState()
    summary = loader.load_state(st, path, items=["it00001", "it00399"])
    assert loader.item_columns(st.data) == ["it00001", "it00399"]
    assert len(st.db_items) == 400 and "item 2/400" in summary
    # seq 1·2에 갈려 있어도 병합으로 한 행에 모인다(§10.1)
    assert st.data.height == 4
    assert st.data["it00399"].null_count() == 0


def test_small_db_still_loads_everything(tmp_path, appdata):
    """item이 LOAD_ALL_ITEMS 이하면 예전처럼 전부 읽는다."""
    path = _big_db(tmp_path, n_items=20)
    st = AppState()
    loader.load_state(st, path, items=["it00001"])
    assert len(loader.item_columns(st.data)) == 20


def test_ensure_items_adds_columns_equal_to_a_full_load(tmp_path, appdata):
    """나중에 고른 item은 열만 더 읽는다 — 값·행 순서가 처음부터 읽은 것과 같다."""
    path = _big_db(tmp_path)
    full = AppState()
    loader.load_state(full, path)
    st = AppState()
    loader.load_state(st, path, items=["it00001"])
    assert loader.ensure_items(st, ["it00001", "it00300", "lot", "없음"]) \
        == ["it00300"]
    assert st.data.height == full.data.height
    assert st.data.select("key", "it00300").sort("key").equals(
        full.data.select("key", "it00300").sort("key"))
    assert loader.ensure_items(st, ["it00300"]) == []        # 이미 있으면 그대로


def test_needed_items_cover_templates_slots_and_explore():
    st = AppState()
    st.explore = PlotSpec(x="A", y="B, C")
    assert st.needed_items() == ["A", "B", "C"]


@pytest.mark.parametrize("gb,want", [(8, "2GB"), (16, "5GB"), (64, "6GB"),
                                     (None, "4GB")])
def test_memory_limit_follows_physical_ram(gb, want):
    total = None if gb is None else gb << 30
    if total is None:
        assert loader.duckdb_memory_limit(0) == want
    else:
        assert loader.duckdb_memory_limit(total) == want
