"""§10.1 — step_seq 때문에 plot이 비는 문제. 이 코드베이스에서 가장 비싼 함정.

x는 `step_seq=1`, y는 `step_seq=2`에 기록되는 경우가 흔하다. seq를 키에 두면
두 행으로 갈려 **x·y가 함께 있는 행이 0개**가 되고 산점도가 통째로 빈다.

2026-10-13부터 **적재할 때 합친다**(`db.merge_seq`). 읽을 때 합치던 window +
`arg_max` SQL은 데이터의 10배 메모리를 써서 OOM의 주범이었다.

  · 저장 키는 (lot, wafer, die_x, die_y, temperature, step_id, site_cnt) — die 하나가 한 행
  · 같은 seq 안의 재측정(retest)은 가장 늦은 값, seq가 다른데 같은 item이면 평균
  · **step_id·온도·site_cnt가 다르면 합치지 않는다**
  · 예전 형식(키에 seq가 있는 DB)은 이어 적재하지 않고 새 파일로 다시 적재하라고 안내한다
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import duckdb
import polars as pl
import pytest

from etreport.data import compat, db, loader
from etreport.model.state import AppState

BASE = {"line_id": "L1", "root_lot_id": "PA100", "wafer_id": "01",
        "chip_x_pos": 3, "chip_y_pos": 4, "temperature": 25.0,
        "step_id": "M2", "step_seq": 1, "total_site_cnt": 9,
        "tkout_time": datetime(2026, 8, 4, 9, 0)}


def _write(tmp_path, rows: list[dict], name: str) -> object:
    p = tmp_path / f"{name}.parquet"
    pl.DataFrame([{**BASE, **r} for r in rows]).write_parquet(p)
    return p


def _load_db(tmp_path, rows: list[dict], name: str = "et.duckdb") -> str:
    """long 행들 → 실제 적재 경로(pivot_and_load) → DB 경로.

    합성 프레임을 직접 넣지 않고 적재 코드를 그대로 태운다 — 적재가 만드는
    key_hash·컬럼 구성까지 그대로여야 분석 로딩을 제대로 검증할 수 있다.
    """
    p = _write(tmp_path, rows, name)
    dbp = tmp_path / name
    with db.Store(dbp) as store:
        db.pivot_and_load(store, [p])
    return str(dbp)


def _loaded(tmp_path, rows: list[dict], name: str = "et.duckdb") -> AppState:
    st = AppState()
    loader.load_state(st, _load_db(tmp_path, rows, name))
    return st


def _vals(st: AppState, *items: str) -> pl.DataFrame:
    """분석 프레임 + item 값 — 값을 읽는 길은 하나다(loader.frame)."""
    return loader.frame(st, items)


def _db(dbp: str) -> pl.DataFrame:
    con = duckdb.connect(dbp, read_only=True)
    try:
        return con.execute("select * from et_data").pl()
    finally:
        con.close()


# ── ★ 본체 ───────────────────────────────────────────────────
def test_x_and_y_on_different_seq_land_in_one_row(tmp_path, appdata):
    """x가 seq 1, y가 seq 2에 있어도 한 행에 실려야 산점도가 그려진다."""
    st = _loaded(tmp_path, [
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.42,
         "tkout_time": datetime(2026, 8, 4, 9, 0)},
        {"step_seq": 2, "item_id": "Ioff", "et_value": 1.5e-9,
         "tkout_time": datetime(2026, 8, 4, 9, 5)},
    ])
    df = _vals(st, "Vt", "Ioff")
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["Vt"] == pytest.approx(0.42)
    assert row["Ioff"] == pytest.approx(1.5e-9)


def test_db_stores_one_row_per_die_with_its_latest_time(tmp_path, appdata):
    """합치는 일은 **적재**가 한다 — DB에는 die마다 한 행, seq 열은 없다.

    tkout_time은 그 측정점의 마지막 측정 시각(표지·조회 기간 기본값이 쓴다).
    """
    got = _db(_load_db(tmp_path, [
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.42,
         "tkout_time": datetime(2026, 8, 4, 9, 0)},
        {"step_seq": 2, "item_id": "Ioff", "et_value": 1.5e-9,
         "tkout_time": datetime(2026, 8, 4, 9, 5)},
    ]))
    assert got.height == 1
    assert "step_seq" not in got.columns
    assert got["tkout_time"][0] == datetime(2026, 8, 4, 9, 5)


@pytest.mark.parametrize("differs", ["step_id", "temperature", "total_site_cnt"])
def test_other_keys_are_never_merged(tmp_path, appdata, differs):
    """step_id·온도·site_cnt가 다르면 다른 측정점 — seq가 달라도 합치지 않는다."""
    other = {"step_id": "M5", "temperature": 85.0, "total_site_cnt": 13}[differs]
    st = _loaded(tmp_path, [
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.42},
        {"step_seq": 2, "item_id": "Ioff", "et_value": 1.5e-9, differs: other},
    ])
    df = _vals(st, "Vt", "Ioff")
    assert df.height == 2
    # 각 행은 자기 item만 갖는다 (섞이면 다른 조건의 값이 한 점으로 합쳐진 것)
    rows = df.sort("Vt", nulls_last=True).to_dicts()
    assert rows[0]["Vt"] == pytest.approx(0.42) and rows[0]["Ioff"] is None
    assert rows[1]["Vt"] is None and rows[1]["Ioff"] == pytest.approx(1.5e-9)


def test_different_die_stays_separate(tmp_path, appdata):
    """die 좌표가 다르면 당연히 다른 점 — 병합이 wafer를 한 점으로 뭉개지 않는다."""
    st = _loaded(tmp_path, [
        {"chip_x_pos": 1, "item_id": "Vt", "et_value": 0.40},
        {"chip_x_pos": 2, "item_id": "Vt", "et_value": 0.41},
        {"chip_x_pos": 3, "chip_y_pos": 9, "item_id": "Vt", "et_value": 0.42},
    ])
    df = _vals(st, "Vt")
    assert df.height == 3
    assert sorted(df["Vt"]) == pytest.approx([0.40, 0.41, 0.42])


def test_retest_keeps_latest_value(tmp_path, appdata):
    """같은 seq에서 두 번 측정되면 tkout_time이 늦은 값만 남는다(평균하지 않는다)."""
    st = _loaded(tmp_path, [
        {"item_id": "Vt", "et_value": 0.10,
         "tkout_time": datetime(2026, 8, 4, 9, 0)},
        {"item_id": "Vt", "et_value": 0.99,
         "tkout_time": datetime(2026, 8, 4, 18, 0)},
    ])
    df = _vals(st, "Vt")
    assert df.height == 1
    assert df["Vt"][0] == pytest.approx(0.99)


def test_same_item_on_two_seqs_is_averaged(tmp_path, appdata):
    """★ seq가 다른데 같은 item이 있으면 평균 — 각 seq는 자기 retest 최신 값으로."""
    st = _loaded(tmp_path, [
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.10,
         "tkout_time": datetime(2026, 8, 4, 9, 0)},
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.40,          # seq 1 retest
         "tkout_time": datetime(2026, 8, 4, 10, 0)},
        {"step_seq": 2, "item_id": "Vt", "et_value": 0.60,
         "tkout_time": datetime(2026, 8, 4, 9, 30)},
    ])
    df = _vals(st, "Vt")
    assert df.height == 1
    assert df["Vt"][0] == pytest.approx(0.50)


def test_averaged_cells_are_counted_for_the_log(tmp_path, appdata):
    """평균을 낸 칸은 item마다 센다 — 겹치지 않는다던 item이 겹치면 로그에 보이게."""
    p = _write(tmp_path, [
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.4},
        {"step_seq": 2, "item_id": "Vt", "et_value": 0.6},
        {"step_seq": 2, "item_id": "Ioff", "et_value": 1e-9},
    ], "avg")
    with db.Store(tmp_path / "avg.duckdb") as store:
        db.pivot_and_load(store, [p])
        assert store.averaged == {"Vt": 1}


def test_null_value_does_not_win(tmp_path, appdata):
    """합친 행의 각 item은 NULL이 아닌 값을 갖는다 (NULL이 평균을 지우면 안 된다)."""
    st = _loaded(tmp_path, [
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.42},
        {"step_seq": 2, "item_id": "Vt", "et_value": None},
        {"step_seq": 2, "item_id": "Ioff", "et_value": 1.5e-9},
    ])
    df = _vals(st, "Vt", "Ioff")
    assert df.height == 1
    assert df["Vt"][0] == pytest.approx(0.42)
    assert df["Ioff"][0] == pytest.approx(1.5e-9)


def test_analysis_key_is_the_db_key_hash(tmp_path, appdata):
    """분석 key = DB의 key_hash — 제외 사이드카가 이 값으로 저장된다."""
    dbp = _load_db(tmp_path, [
        {"chip_x_pos": 1, "item_id": "Vt", "et_value": 0.40},
        {"chip_x_pos": 2, "item_id": "Vt", "et_value": 0.41},
    ])
    st = AppState()
    loader.load_state(st, dbp)
    assert set(st.data["key"]) == set(_db(dbp)["key_hash"])


def test_merged_db_is_read_without_window_or_group_by(tmp_path, appdata):
    """적재 때 합친 DB는 평평한 SELECT로 읽는다 — 읽기 OOM의 원인이 사라진 자리."""
    dbp = _load_db(tmp_path, [{"item_id": "Vt", "et_value": 0.4}])
    with loader.readonly_query(dbp) as con:
        prof = compat.profile(con, compat.pick_table(con))
    assert prof.merged
    sql = compat.select_sql(prof).upper()
    assert "QUALIFY" not in sql and "GROUP BY" not in sql and "ARG_MAX" not in sql


# ── 이어 적재 ─────────────────────────────────────────────────
def test_later_measurement_replaces_and_older_only_fills_gaps(tmp_path, appdata):
    """같은 die가 다시 들어오면 **더 늦게 잰 값**이 이기고, 이른 값은 빈 칸만 채운다.

    며칠씩 겹쳐 도는 예약 추출에서 retest가 묻히지 않아야 한다.
    """
    dbp = tmp_path / "et.duckdb"
    day1 = _write(tmp_path, [
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.10},
        {"step_seq": 2, "item_id": "Ioff", "et_value": 1.0,
         "tkout_time": datetime(2026, 8, 4, 9, 30)},
    ], "d1")
    day2 = _write(tmp_path, [                      # 다음 날 seq 1 retest + 새 item
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.90,
         "tkout_time": datetime(2026, 8, 5, 8, 0)},
        {"step_seq": 1, "item_id": "Gm", "et_value": 5.0,
         "tkout_time": datetime(2026, 8, 5, 8, 0)},
    ], "d2")
    old = _write(tmp_path, [                       # 예전 기간을 다시 받은 것
        {"step_seq": 1, "item_id": "Vt", "et_value": 0.50,
         "tkout_time": datetime(2026, 8, 3, 8, 0)},
        {"step_seq": 1, "item_id": "Ron", "et_value": 7.0,
         "tkout_time": datetime(2026, 8, 3, 8, 0)},
    ], "d0")
    for p in (day1, day2, old):
        with db.Store(dbp) as store:
            db.pivot_and_load(store, [p])

    got = _db(str(dbp))
    assert got.height == 1
    row = got.row(0, named=True)
    assert row["Vt"] == pytest.approx(0.90)          # 늦은 retest가 이겼다
    assert row["Ioff"] == pytest.approx(1.0)         # 들어온 쪽에 없던 값은 그대로
    assert row["Gm"] == pytest.approx(5.0)
    assert row["Ron"] == pytest.approx(7.0)          # 이른 데이터도 빈 칸은 채운다
    assert row["tkout_time"] == datetime(2026, 8, 5, 8, 0)


def test_reloading_the_same_file_changes_nothing(tmp_path, appdata):
    p = _write(tmp_path, [{"item_id": "Vt", "et_value": 0.4}], "same")
    dbp = tmp_path / "et.duckdb"
    with db.Store(dbp) as store:
        assert db.pivot_and_load(store, [p]) == 1
    with db.Store(dbp) as store:
        assert db.pivot_and_load(store, [p]) == 0
        assert store.filled == 0


# ── 예전 형식 DB ──────────────────────────────────────────────
def _old_layout_db(tmp_path) -> str:
    """키에 step_seq·시각이 있던 예전 형식 — seq마다 한 행씩."""
    p = tmp_path / "old.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""create table et_data(
        root_lot_id varchar, wafer_id varchar, chip_x_pos int, chip_y_pos int,
        temperature double, step_id varchar, step_seq varchar,
        total_site_cnt int, tkout_time timestamp, line_id varchar,
        key_hash varchar, Vt double, Ioff double)""")
    con.execute("""insert into et_data values
        ('PA1','01',1,1,25,'M2','1',9,'2026-08-01 09:00','L1','k1',0.5,NULL),
        ('PA1','01',1,1,25,'M2','2',9,'2026-08-01 09:05','L1','k2',NULL,1e-9)""")
    con.close()
    return str(p)


def test_old_layout_db_refuses_append_with_guidance(tmp_path, appdata):
    """예전 형식에 새 형식을 섞지 않는다 — 열기부터 막고 새 파일을 쓰라고 말한다."""
    dbp = _old_layout_db(tmp_path)
    assert "새 DB 파일" in db.format_problem(dbp)
    with pytest.raises(RuntimeError, match="새 DB 파일"):
        db.Store(dbp)


def test_pipeline_checks_the_db_before_extracting(tmp_path, appdata):
    """추출은 몇 분씩 걸린다 — 그 뒤에 적재에서 실패하면 안 되므로 먼저 확인한다."""
    from etreport.data import pipeline
    preset = SimpleNamespace(db_path=_old_layout_db(tmp_path))
    with pytest.raises(RuntimeError, match="새 DB 파일"):
        pipeline.run(preset, datetime(2026, 8, 1).date(),
                     datetime(2026, 8, 1).date(), catalog=None)


def test_old_layout_db_is_read_without_merging(tmp_path, appdata):
    """예전 DB도 열린다. 다만 seq는 합치지 않는다(arg_max 병합은 폐기)."""
    st = AppState()
    loader.load_state(st, _old_layout_db(tmp_path))
    assert not st.profile.merged
    df = _vals(st, "Vt", "Ioff")
    assert df.height == 2
    assert df.drop_nulls(["Vt", "Ioff"]).height == 0


def test_apply_tells_to_reload_an_old_layout_db(tmp_path, appdata):
    """[적용] 결과에 '새 파일로 다시 적재' 안내가 붙는다 — 산점도가 비는 이유다."""
    from etreport.config.settings import AnalysisConfig
    from etreport.model.session import apply_config

    rep = apply_config(AppState(), AnalysisConfig(name="t",
                                                  db_path=_old_layout_db(tmp_path)))
    assert rep.ok
    assert any("다시 적재" in n for n in rep.notes)


# ── 다른 스키마들 ────────────────────────────────────────────
def test_hand_made_long_table_is_not_merged(tmp_path, appdata):
    """손코딩 시절 long 테이블도 이제 seq를 합치지 않는다 — 읽기는 그대로 된다."""
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
    df = _vals(st, "VT_LIN", "IOFF")
    assert df.height == 4
    assert df["VT_LIN"].drop_nulls().len() == 2 and df["IOFF"].drop_nulls().len() == 2


def test_schema_without_seq_is_left_alone(tmp_path, appdata):
    """step_seq가 없는 스키마는 그대로 읽는다 — 잘못 뭉치면 데이터를 조용히 잃는다."""
    p = tmp_path / "noseq.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""create table et_data(
        lot varchar, slot_no varchar, x_pos int, y_pos int,
        create_dttm timestamp, VT double, IOFF double)""")
    con.execute("""insert into et_data values
        ('PA1','01',1,1,'2026-08-01 09:00',0.5,1e-9),
        ('PA1','01',2,2,'2026-08-01 09:00',0.6,2e-9)""")
    con.close()

    with loader.readonly_query(str(p)) as ro:
        assert "GROUP BY" not in compat.select_sql(compat.profile(ro, "et_data"))
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


def _pipeline(tmp_path, rf, retest: bool = False, rows: list[dict] | None = None
              ) -> AppState:
    """seq 1에 Vt·Id, seq 2에 Ioff — die 3개 × wafer 2장. 실제 경로를 그대로 탄다."""
    from etreport.data import reformatter
    if rows is None:
        rows = []
        for w in ("01", "02"):
            for x in range(3):
                t1 = datetime(2026, 8, 4, 9, 0)
                for it, v in (("VT", 0.4 + x), ("ID", 5.0 + x)):
                    rows.append({"wafer_id": w, "chip_x_pos": x, "step_seq": 1,
                                 "tkout_time": t1, "item_id": it, "et_value": v})
                if retest:      # seq 1 재측정 — 최신 값(Id=100+x)이 이겨야 한다
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
    with db.Store(dbp) as store:
        db.pivot_and_load(store, [p])
    st = AppState()
    st.rf = rf
    loader.load_state(st, str(dbp))
    return st


def test_cross_seq_addp_survives_to_table_and_plot(tmp_path, appdata):
    """seq 1의 Id와 seq 2의 Ioff로 만든 ADDP가 적재 전에 지워지면 안 된다.

    예전에는 리포메팅이 seq별 행에서 수식을 풀어 결과가 전부 NULL → drop_nulls로
    item째 사라졌다. 표의 CAT1이 통째로 비고 그 item을 축으로 쓴 산점도가 비었다.
    """
    from etreport.model.aggregate import wafer_stats

    st = _pipeline(tmp_path, _rf(("Ratio", "{Id}/{Ioff}"),
                                 ("Ratio2", "{Ratio}*2")))   # ADDP-on-ADDP
    df = _vals(st, "Vt", "Id", "Ratio", "Ratio2")
    assert df.height == 6
    got = {r["Id"]: (r["Ratio"], r["Ratio2"]) for r in df.iter_rows(named=True)}
    for x in range(3):
        exp = (5.0 + x) / (1.0 + x)
        assert got[5.0 + x] == pytest.approx((exp, 2 * exp))
    assert df.drop_nulls(["Vt", "Ratio"]).height == 6          # 산점도 점 수
    ws = wafer_stats(df, set(), ["Ratio"])
    assert ws.get("Ratio", "PA100", "01") == pytest.approx((5 + 3 + 7 / 3) / 3)


def test_cross_seq_addp_uses_latest_retest(tmp_path, appdata):
    """재측정이 있으면 분석 화면과 같은 최신 값으로 계산한다."""
    st = _pipeline(tmp_path, _rf(("Ratio", "{Id}/{Ioff}")), retest=True)
    for r in _vals(st, "Id", "Ioff", "Ratio").iter_rows(named=True):
        assert r["Ratio"] == pytest.approx(r["Id"] / r["Ioff"])
        assert r["Id"] >= 100


@pytest.mark.parametrize("formula", ["{Id}/{Ioff}", "{Id}/{Vt}"])
def test_addp_lands_on_the_merged_row(tmp_path, appdata, formula):
    """seq를 넘나들든 seq 안에서 풀리든 ADDP는 die의 그 한 행에 실린다."""
    st = _pipeline(tmp_path, _rf(("New", formula)))
    df = _vals(st, "Vt", "Id", "Ioff", "New")
    assert df.height == 6
    assert df.drop_nulls(["Vt", "Id", "Ioff", "New"]).height == 6


def test_cross_seq_addp_inputs_follow_the_seq_average(tmp_path, appdata):
    """같은 REAL이 두 seq에 있으면 저장 값(평균)과 수식 입력이 같아야 한다."""
    t = datetime(2026, 8, 4, 9, 0)
    rows = [{"step_seq": 1, "tkout_time": t, "item_id": "ID", "et_value": 5.0},
            {"step_seq": 2, "tkout_time": t, "item_id": "ID", "et_value": 7.0},
            {"step_seq": 3, "tkout_time": t, "item_id": "IOFF", "et_value": 2.0}]
    st = _pipeline(tmp_path, _rf(("Ratio", "{Id}/{Ioff}")), rows=rows)
    row = _vals(st, "Id", "Ratio").row(0, named=True)
    assert row["Id"] == pytest.approx(6.0)
    assert row["Ratio"] == pytest.approx(3.0)


def test_cross_seq_std_groups_without_seq(tmp_path, appdata):
    """Std()도 seq를 넘나들면 seq를 뺀 묶음(lot·wafer·step·site·온도)으로 계산한다."""
    import statistics

    st = _pipeline(tmp_path, _rf(("Spread", "Std({Id},{Ioff})")))
    exp = statistics.stdev([5.0, 6.0, 7.0, 1.0, 2.0, 3.0])
    assert _vals(st, "Spread")["Spread"].to_list() == pytest.approx([exp] * 6)


# ── step_seq가 NULL로 들어온 추출 ─────────────────────────────
def test_null_seq_still_merges(tmp_path, appdata):
    """★ step_seq가 **전부 NULL**이어도 합쳐져야 한다.

    조회가 seq를 못 받아 오면 NULL이 들어온다. 예전 읽기 병합은 이때 늦게 찍힌
    행만 남겨 먼저 찍힌 item이 통째로 사라졌다. 적재 병합은 (seq, item)마다
    최신을 고르므로 서로 다른 item은 둘 다 산다.
    """
    st = _loaded(tmp_path, [
        {"step_seq": None, "item_id": "Vt", "et_value": 0.42,
         "tkout_time": datetime(2026, 8, 4, 9, 0)},
        {"step_seq": None, "item_id": "Ioff", "et_value": 1.5e-9,
         "tkout_time": datetime(2026, 8, 4, 9, 5)},
    ])
    df = _vals(st, "Vt", "Ioff")
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["Vt"] == pytest.approx(0.42)
    assert row["Ioff"] == pytest.approx(1.5e-9)


def test_null_seq_retest_still_keeps_latest(tmp_path, appdata):
    """seq가 NULL이어도 **같은 item**을 두 번 찍었으면 늦은 값이 이긴다."""
    st = _loaded(tmp_path, [
        {"step_seq": None, "item_id": "Vt", "et_value": 0.10,
         "tkout_time": datetime(2026, 8, 4, 9, 0)},
        {"step_seq": None, "item_id": "Vt", "et_value": 0.99,
         "tkout_time": datetime(2026, 8, 4, 18, 0)},
    ])
    df = _vals(st, "Vt")
    assert df.height == 1
    assert df["Vt"][0] == pytest.approx(0.99)
