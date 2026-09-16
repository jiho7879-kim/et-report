"""§10.1 — step_seq 때문에 plot이 비는 문제. 이 코드베이스에서 가장 비싼 함정.

x는 `step_seq=1`, y는 `step_seq=2`에 기록되는 경우가 흔하다. seq를 키에 두면
두 행으로 갈려 **x·y가 함께 있는 행이 0개**가 되고 산점도가 통째로 빈다.
사양이 요구하는 세 가지를 각각 못 박는다.

  1) 조회 SQL이 키 컬럼을 표준 이름으로 함께 SELECT   → test_pipeline_duckdb
  2) retest 중복 제거(QUALIFY) 파티션에 step_seq 포함 → 아래 (빼면 정상 행이 사라진다)
  3) 로드 후 step_seq만 다른 행을 한 측정점으로 병합  → 아래
     키는 (lot, wafer, die_x, die_y, temperature, step_id, site_cnt).
     **step_id·온도·site_cnt가 다르면 합치지 않는다.**
"""
from __future__ import annotations

from datetime import datetime

import duckdb
import polars as pl
import pytest

from etreport.data import db, loader
from etreport.model.state import AppState

BASE = {"line_id": "L1", "root_lot_id": "PA100", "wafer_id": "01",
        "chip_x_pos": 3, "chip_y_pos": 4, "temperature": 25.0,
        "step_id": "M2", "step_seq": 1, "total_site_cnt": 9,
        "tkout_time": datetime(2026, 8, 4, 9, 0)}


def _load_db(tmp_path, rows: list[dict], name: str = "et.duckdb") -> str:
    """long 행들 → 실제 적재 경로(pivot_and_load) → DB 경로.

    합성 프레임을 직접 넣지 않고 적재 코드를 그대로 태운다 — 적재가 만드는
    key_hash·컬럼 구성까지 그대로여야 분석 로딩을 제대로 검증할 수 있다.
    """
    long = pl.DataFrame([{**BASE, **r} for r in rows])
    p = tmp_path / f"{name}.parquet"
    long.write_parquet(p)
    dbp = tmp_path / name
    store = db.Store(dbp)
    try:
        db.pivot_and_load(store, [p])
    finally:
        store.close()
    return str(dbp)


def _loaded(tmp_path, rows: list[dict], name: str = "et.duckdb") -> AppState:
    st = AppState()
    loader.load_state(st, _load_db(tmp_path, rows, name))
    return st


# ── ★ 본체 ───────────────────────────────────────────────────
def test_x_and_y_on_different_seq_land_in_one_row(tmp_path, appdata):
    """x가 seq 1, y가 seq 2에 있어도 한 행에 실려야 산점도가 그려진다.

    시각도 다르게 둔다 — QUALIFY 파티션에서 step_seq가 빠지면 여기서 한 행이
    'retest 구버전'으로 지워져 y가 통째로 사라진다.
    """
    st = _loaded(tmp_path, [
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.42,
         "tkout_time": datetime(2026, 8, 4, 9, 0)},
        {"step_seq": 2, "item_id": "Ioff", "et_value": 1.5e-9,
         "tkout_time": datetime(2026, 8, 4, 9, 5)},
    ])

    assert st.data.height == 1
    row = st.data.row(0, named=True)
    assert row["Vt"] == pytest.approx(0.42)
    assert row["Ioff"] == pytest.approx(1.5e-9)
    # 산점도가 실제로 점을 얻는지 — 두 값이 모두 있는 행 수가 곧 점의 수다
    assert st.data.filter(pl.col("Vt").is_not_null()
                          & pl.col("Ioff").is_not_null()).height == 1


@pytest.mark.parametrize("differs", ["step_id", "temperature", "total_site_cnt"])
def test_other_keys_are_never_merged(tmp_path, appdata, differs):
    """step_id·온도·site_cnt가 다르면 다른 측정점 — seq가 달라도 합치지 않는다."""
    other = {"step_id": "M5", "temperature": 85.0, "total_site_cnt": 13}[differs]
    st = _loaded(tmp_path, [
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.42},
        {"step_seq": 2, "item_id": "Ioff", "et_value": 1.5e-9, differs: other},
    ])

    assert st.data.height == 2
    # 각 행은 자기 item만 갖는다 (섞이면 다른 조건의 값이 한 점으로 합쳐진 것)
    rows = st.data.sort("Vt", nulls_last=True).to_dicts()
    assert rows[0]["Vt"] == pytest.approx(0.42) and rows[0]["Ioff"] is None
    assert rows[1]["Vt"] is None and rows[1]["Ioff"] == pytest.approx(1.5e-9)


def test_different_die_stays_separate(tmp_path, appdata):
    """die 좌표가 다르면 당연히 다른 점 — 병합이 wafer를 한 점으로 뭉개지 않는다."""
    st = _loaded(tmp_path, [
        {"chip_x_pos": 1, "item_id": "Vt", "et_value": 0.40},
        {"chip_x_pos": 2, "item_id": "Vt", "et_value": 0.41},
        {"chip_x_pos": 3, "chip_y_pos": 9, "item_id": "Vt", "et_value": 0.42},
    ])
    assert st.data.height == 3
    assert sorted(st.data["Vt"]) == pytest.approx([0.40, 0.41, 0.42])


def test_retest_keeps_latest_value(tmp_path, appdata):
    """같은 키(seq 포함)로 두 번 측정되면 tkout_time이 늦은 값만 남는다."""
    st = _loaded(tmp_path, [
        {"item_id": "Vt", "et_value": 0.10,
         "tkout_time": datetime(2026, 8, 4, 9, 0)},
        {"item_id": "Vt", "et_value": 0.99,
         "tkout_time": datetime(2026, 8, 4, 18, 0)},
    ])
    assert st.data.height == 1
    assert st.data["Vt"][0] == pytest.approx(0.99)


def test_merge_takes_first_non_null_per_item(tmp_path, appdata):
    """병합된 행의 각 item은 NULL이 아닌 값을 갖는다 (NULL이 이기면 안 된다)."""
    st = _loaded(tmp_path, [
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.42},
        {"step_seq": 2, "item_id": "Vt", "et_value": None},
        {"step_seq": 2, "item_id": "Ioff", "et_value": 1.5e-9},
    ])
    assert st.data.height == 1
    assert st.data["Vt"][0] == pytest.approx(0.42)
    assert st.data["Ioff"][0] == pytest.approx(1.5e-9)


def test_key_is_unchanged_when_nothing_merges(tmp_path, appdata):
    """seq가 하나뿐인(=지금까지 정상 동작하던) DB에서는 key가 예전 그대로여야 한다.

    제외 포인트 사이드카가 key로 저장되므로, 키가 바뀌면 사용자의 제외 이력이
    조용히 사라진다.
    """
    dbp = _load_db(tmp_path, [
        {"chip_x_pos": 1, "item_id": "Vt", "et_value": 0.40},
        {"chip_x_pos": 2, "item_id": "Vt", "et_value": 0.41},
    ])
    con = duckdb.connect(dbp, read_only=True)
    hashes = {r[0] for r in con.execute("select key_hash from et_data").fetchall()}
    con.close()

    st = AppState()
    loader.load_state(st, dbp)
    assert set(st.data["key"]) == hashes


# ── 다른 스키마들 ────────────────────────────────────────────
def test_long_table_merges_seq_too(tmp_path, appdata):
    """손코딩 시절 long 테이블(item_id/value)도 같은 규칙으로 합친다."""
    p = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""create table et_data(
        lot_id varchar, wafer varchar, die_x int, die_y int, step_id varchar,
        step_seq int, meas_time timestamp, item varchar, val double)""")
    con.execute("""insert into et_data values
        ('PA1','01',1,1,'M2',1,'2026-08-01 09:00','VT_LIN',0.51),
        ('PA1','01',1,1,'M2',2,'2026-08-01 09:03','IOFF',1e-9),
        ('PA1','02',1,1,'M2',1,'2026-08-01 09:05','VT_LIN',0.49),
        ('PA1','02',1,1,'M2',2,'2026-08-01 09:08','IOFF',2e-9)""")
    con.close()

    st = AppState()
    loader.load_state(st, str(p))
    assert st.profile.is_long
    assert st.data.height == 2                     # wafer당 한 점
    for row in st.data.iter_rows(named=True):
        assert row["VT_LIN"] is not None and row["IOFF"] is not None


def test_schema_without_seq_is_left_alone(tmp_path, appdata):
    """step_seq가 없는 스키마는 병합하지 않는다 — 합칠 이유가 없고, 잘못 뭉치면
    데이터를 조용히 잃는다."""
    from etreport.data import compat

    p = tmp_path / "noseq.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""create table et_data(
        lot varchar, slot_no varchar, x_pos int, y_pos int,
        create_dttm timestamp, VT double, IOFF double)""")
    con.execute("""insert into et_data values
        ('PA1','01',1,1,'2026-08-01 09:00',0.5,1e-9),
        ('PA1','01',2,2,'2026-08-01 09:00',0.6,2e-9)""")
    con.close()

    ro = loader.open_readonly(str(p))
    prof = compat.profile(ro, "et_data")
    assert not compat.merges_seq(prof)
    assert "GROUP BY" not in compat.select_sql(prof)
    ro.close()

    st = AppState()
    loader.load_state(st, str(p))
    assert st.data.height == 2


# ── ADDP가 seq를 넘나들 때 (리포메팅 → 적재 → 로딩 전 구간) ──────
def _rf(*addp: tuple[str, str]):
    """REAL Vt·Id(seq 1)·Ioff(seq 2) + 주어진 ADDP들."""
    from etreport.data import reformatter
    n = 3 + len(addp)
    return reformatter.from_frame(pl.DataFrame({
        "CATEGORY": ["REAL"] * 3 + ["ADDP"] * len(addp),
        "ITEMID": ["VT", "ID", "IOFF"] + [None] * len(addp),
        "ALIAS": ["Vt", "Id", "Ioff"] + [a for a, _ in addp],
        "ABSOLUTE": [None] * n, "SCALE FACTOR": [None] * n,
        "ADDP FORM": [None] * 3 + [f for _, f in addp],
        "UNIT": [None] * n, "SPECLOW": [None] * n, "SPECHIGH": [None] * n,
        "TARGET": [None] * n,
    }))


def _pipeline(tmp_path, rf, retest: bool = False) -> AppState:
    """seq 1에 Vt·Id, seq 2에 Ioff — die 3개 × wafer 2장. 실제 경로를 그대로 탄다."""
    from etreport.data import reformatter
    rows = []
    for w in ("01", "02"):
        for x in range(3):
            t1 = datetime(2026, 8, 4, 9, 0)
            for it, v in (("VT", 0.4 + x), ("ID", 5.0 + x)):
                rows.append({"wafer_id": w, "chip_x_pos": x, "step_seq": 1,
                             "tkout_time": t1, "item_id": it, "et_value": v})
            if retest:          # seq 1 재측정 — 최신 값(Id=100+x)이 이겨야 한다
                rows.append({"wafer_id": w, "chip_x_pos": x, "step_seq": 1,
                             "tkout_time": datetime(2026, 8, 4, 18, 0),
                             "item_id": "ID", "et_value": 100.0 + x})
            rows.append({"wafer_id": w, "chip_x_pos": x, "step_seq": 2,
                         "tkout_time": datetime(2026, 8, 4, 9, 30),
                         "item_id": "IOFF", "et_value": 1.0 + x})
    long = pl.DataFrame([{**BASE, **r} for r in rows])
    out = reformatter.apply(rf, long)
    p = tmp_path / "rf.parquet"
    out.write_parquet(p)
    dbp = tmp_path / "et.duckdb"
    store = db.Store(dbp)
    try:
        db.pivot_and_load(store, [p])
    finally:
        store.close()
    st = AppState()
    st.rf = rf
    loader.load_state(st, str(dbp))
    st.db_file = str(dbp)
    return st


def _counts(st: AppState, *cols: str) -> list[tuple]:
    """seq별로 각 컬럼에 값이 든 행 수 — DB에 무엇이 어느 seq로 저장됐나."""
    sel = ", ".join(f"count({c})" for c in cols)
    con = duckdb.connect(st.db_file, read_only=True)
    try:
        return con.execute(f"select step_seq, {sel} from et_data "
                           "group by 1 order by 1").fetchall()
    finally:
        con.close()


def test_cross_seq_addp_survives_to_table_and_plot(tmp_path, appdata):
    """seq 1의 Id와 seq 2의 Ioff로 만든 ADDP가 적재 전에 지워지면 안 된다.

    예전에는 리포메팅이 seq별 행에서 수식을 풀어 결과가 전부 NULL → drop_nulls로
    item째 사라졌다. 표의 CAT1이 통째로 비고 그 item을 축으로 쓴 산점도가 비었다.
    """
    from etreport.model.aggregate import wafer_stats

    st = _pipeline(tmp_path, _rf(("Ratio", "{Id}/{Ioff}"),
                                 ("Ratio2", "{Ratio}*2")))   # ADDP-on-ADDP
    assert st.data.height == 6
    got = {r["Id"]: (r["Ratio"], r["Ratio2"]) for r in st.data.iter_rows(named=True)}
    for x in range(3):
        exp = (5.0 + x) / (1.0 + x)
        assert got[5.0 + x] == pytest.approx((exp, 2 * exp))
    assert st.data.drop_nulls(["Vt", "Ratio"]).height == 6          # 산점도 점 수
    ws = wafer_stats(st.data, set(), ["Ratio"])
    assert ws.get("Ratio", "PA100", "01") == pytest.approx((5 + 3 + 7 / 3) / 3)


def test_cross_seq_addp_uses_latest_retest(tmp_path, appdata):
    """재측정이 있으면 분석 화면과 같은 최신 값으로 계산한다."""
    st = _pipeline(tmp_path, _rf(("Ratio", "{Id}/{Ioff}")), retest=True)
    for r in st.data.iter_rows(named=True):
        assert r["Ratio"] == pytest.approx(r["Id"] / r["Ioff"])
        assert r["Id"] >= 100


def test_cross_seq_addp_is_stored_with_every_seq(tmp_path, appdata):
    """REAL은 원래 seq에 그대로, 합쳐 계산한 ADDP는 die의 seq 행마다 같은 값."""
    st = _pipeline(tmp_path, _rf(("Ratio", "{Id}/{Ioff}")))
    assert _counts(st, "Id", "Ioff", "Ratio") == [(1, 6, 0, 6), (2, 0, 6, 6)]


def test_same_seq_addp_is_unchanged(tmp_path, appdata):
    """seq 안에서 풀리는 ADDP는 예전처럼 그 seq 행에만 저장된다."""
    st = _pipeline(tmp_path, _rf(("Gm", "{Id}/{Vt}")))
    assert _counts(st, "Gm") == [(1, 6), (2, 0)]


def test_cross_seq_std_groups_without_seq(tmp_path, appdata):
    """Std()도 seq를 넘나들면 seq를 뺀 묶음(lot·wafer·step·site·온도)으로 계산한다."""
    import statistics

    st = _pipeline(tmp_path, _rf(("Spread", "Std({Id},{Ioff})")))
    exp = statistics.stdev([5.0, 6.0, 7.0, 1.0, 2.0, 3.0])
    assert st.data["Spread"].to_list() == pytest.approx([exp] * 6)
