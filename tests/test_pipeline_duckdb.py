"""추출 이후 파이프라인 전체 — 리포메팅 → DuckDB 적재 → 분석 화면 로딩.

bdq(추출)와 Excel(리포메터 읽기)만 합성으로 대체하면 나머지는 전부 실제
코드가 돈다. 리포메터가 사내 PC에서 "정상 동작하는가"를 확인하기 전에,
그 결과가 DB와 분석 화면까지 제대로 흘러가는지를 여기서 미리 못 박는다.
"""
from __future__ import annotations

import polars as pl
import pytest

from etreport.data import compat, db, loader
from etreport.data.reformatter import apply as rf_apply
from tests.factory import make_long, make_reformatter


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    """합성 long → 리포메팅 → parquet → DuckDB 적재. (버킷을 여러 개로 강제)"""
    monkeypatch.setattr(db, "TARGET_CELLS", 200)     # 일부러 잘게 쪼갠다
    rf = make_reformatter(n_real=25, n_addp=6, seed=11)
    src = make_long([r.itemid for r in rf.reals()],
                    lots=1, wafers=5, chips=4, seed=11, null_rate=0.05)
    out = rf_apply(rf, src)
    p = tmp_path / "raw_20260801_20260801_test.parquet"
    out.write_parquet(p)

    dbp = tmp_path / "et.duckdb"
    store = db.Store(dbp)
    n = db.pivot_and_load(store, [p])
    store.con.close()
    return {"db": str(dbp), "rf": rf, "long": out, "rows": n, "parquet": p}


def test_load_creates_et_data_table(loaded):
    con = loader.open_readonly(loaded["db"])
    tables = compat.list_tables(con)
    assert "et_data" in tables            # 손코딩 시절과 같은 이름
    assert "v_latest" in tables           # retest 최신 뷰
    assert compat.pick_table(con) == "et_data"

    n_key = con.execute("select count(*) from et_data").fetchone()[0]
    assert n_key == 5 * 4                 # wafer 5 × chip 4 = 키 20개
    assert loaded["rows"] == n_key
    con.close()


def test_alias_columns_land_in_db(loaded):
    con = loader.open_readonly(loaded["db"])
    cols = {r[0] for r in con.execute(
        "select column_name from information_schema.columns "
        "where table_name='et_data'").fetchall()}
    for r in loaded["rf"].rules:
        assert r.alias in cols, f"{r.alias}가 DB에 없다"
    assert "key_hash" in cols
    for k in db.KEY9:
        assert k in cols
    con.close()


def test_reload_same_file_does_not_duplicate(loaded, monkeypatch):
    """같은 parquet를 다시 적재해도 key_hash ANTI JOIN으로 행이 늘지 않는다."""
    monkeypatch.setattr(db, "TARGET_CELLS", 200)
    store = db.Store(loaded["db"])
    before = store.con.execute("select count(*) from et_data").fetchone()[0]
    db.pivot_and_load(store, [loaded["parquet"]])
    after = store.con.execute("select count(*) from et_data").fetchone()[0]
    store.con.close()
    assert after == before


def test_analysis_load_state(loaded, appdata):
    """분석 화면이 읽는 경로 — 읽기 전용 연결 + wide 프레임."""
    from etreport.model.state import AppState

    st = AppState()
    summary = loader.load_state(st, loaded["db"])

    assert "et_data" in summary and "포인트" in summary
    assert st.data is not None
    assert st.data.columns[:4] == ["key", "lot", "wafer", "gid"]
    assert st.data.height == 20
    items = loader.item_columns(st.data)
    for r in loaded["rf"].rules:
        assert r.alias in items
    assert st.data["lot"].unique().to_list() == ["PA2600"]
    assert sorted(st.data["wafer"].unique()) == ["01", "02", "03", "04", "05"]

    # 읽기 전용 — 쓰기가 막혀 있어야 한다
    import duckdb
    with loader.readonly_query(loaded["db"]) as con, pytest.raises(duckdb.Error):
        con.execute("create table x(a int)")


def test_values_survive_the_round_trip(loaded, appdata):
    """리포메팅 결과(long)와 DB에서 읽은 wide의 값이 같아야 한다."""
    from etreport.model.state import AppState

    st = AppState()
    loader.load_state(st, loaded["db"])

    alias = loaded["rf"].addps()[0].alias
    expect = (loaded["long"].filter(pl.col("item_id") == alias)
              .select(["wafer_id", "value"]).sort("wafer_id"))
    got = (st.data.select(["wafer", alias]).drop_nulls()
           .rename({"wafer": "wafer_id", alias: "value"}).sort("wafer_id"))
    assert got.height == expect.height
    assert (got.sort(["wafer_id", "value"])["value"].to_list()
            == pytest.approx(expect.sort(["wafer_id", "value"])["value"].to_list()))


def test_exclusions_are_sidecar_not_in_db(loaded, appdata):
    """제외는 원본 DB를 건드리지 않고 %APPDATA% JSON에 남는다."""
    from etreport.data import exclusions
    from etreport.model.state import AppState

    st = AppState()
    loader.load_state(st, loaded["db"])
    key = st.data["key"][0]

    loader.sync_exclusion(st, key, True, reason="테스트")
    assert exclusions.load(loaded["db"])[key]["reason"] == "테스트"
    assert (appdata / "exclusions").is_dir()

    st2 = AppState()
    loader.load_state(st2, loaded["db"])         # 다시 열어도 유지
    assert key in st2.excluded
    assert st2.active().height == st2.data.height - 1

    loader.sync_exclusion(st2, key, False)
    assert exclusions.load(loaded["db"]) == {}


def test_long_form_db_is_pivoted_on_read(tmp_path, appdata):
    """손코딩 시절의 long 테이블(item_id/value)도 읽을 수 있어야 한다."""
    import duckdb

    from etreport.model.state import AppState

    p = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""create table et_data(
        lot_id varchar, wafer varchar, die_x int, die_y int,
        meas_time timestamp, item varchar, val double)""")
    con.execute("""insert into et_data values
        ('PA1','01',1,1,'2026-08-01 09:00','VT_LIN',0.51),
        ('PA1','01',1,1,'2026-08-01 09:00','IOFF',1e-9),
        ('PA1','02',1,1,'2026-08-01 09:05','VT_LIN',0.49),
        ('PA1','02',1,1,'2026-08-01 09:05','IOFF',2e-9)""")
    con.close()

    st = AppState()
    loader.load_state(st, str(p))
    assert st.profile.is_long
    assert st.data.columns[:4] == ["key", "lot", "wafer", "gid"]
    assert set(loader.item_columns(st.data)) == {"VT_LIN", "IOFF"}
    assert st.data.height == 2
    assert sorted(st.data["VT_LIN"]) == [0.49, 0.51]


def test_table_profile_recognises_aliased_columns(tmp_path):
    import duckdb

    p = tmp_path / "aliased.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""create table et_data(
        lot varchar, slot_no varchar, x_pos int, y_pos int,
        create_dttm timestamp, VT double, IOFF double)""")
    con.execute("insert into et_data values ('PA1','01',1,1,'2026-08-01',0.5,1e-9)")
    con.close()

    con = loader.open_readonly(str(p))
    prof = compat.profile(con, "et_data")
    assert prof.lot_col == "lot"
    assert prof.wafer_col == "slot_no"
    assert prof.roles["time"] == "create_dttm"
    assert set(prof.items) == {"VT", "IOFF"}
    assert not prof.is_long
    con.close()
