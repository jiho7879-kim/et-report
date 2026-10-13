"""작업 세트(2026-10-13) — [적용]은 키만 메모리에, 값은 임시 parquet에서 열 단위로.

[적용]이 `행 × item` 프레임을 통째로 올리고 제외·그룹 분할·집계가 저마다 한 벌씩
복사하던 것이 lot 두 개만 골라도 OOM이던 나머지 절반이다. 여기서 지키는 것:

  · [적용] 뒤 `state.data`에는 키 열만 있다 — item 값은 작업 세트 parquet에 있다
  · 값은 `loader.frame()`으로 그 열만 읽는다 — 값은 DB와 같고 ABSOLUTE도 걸린다
  · 같은 DB·lot·item이면 다시 뜨지 않고, 파일은 최근 `KEEP`벌만 남는다
  · 묶음으로 나눈 집계·이상치·커버리지 = 한 번에 계산한 결과
"""
from __future__ import annotations

import os
from datetime import datetime

import duckdb
import polars as pl
import pytest

from etreport.data import db, loader, workset
from etreport.model.state import AppState

ITEMS = ["Vt", "Id", "Ioff", "Gm"]


def _long(lots=("PA1", "PA2"), wafers=3, dies=6, items=ITEMS, day=4) -> pl.DataFrame:
    """값 = lot·wafer·die·item에서 결정된다(어디서 왔는지 바로 안다). Ioff는 음수."""
    rows = []
    for li, lot in enumerate(lots):
        for w in range(wafers):
            for d in range(dies):
                for ii, it in enumerate(items):
                    v = li * 1000 + w * 100 + d * 10 + ii + 0.5
                    rows.append({
                        "line_id": "L1", "root_lot_id": lot, "wafer_id": f"{w + 1:02d}",
                        "chip_x_pos": d, "chip_y_pos": 0,
                        "temperature": 25.0 if d % 2 else 85.0,
                        "step_id": "M2", "step_seq": "1" if ii % 2 else "2",
                        "total_site_cnt": 9,
                        "tkout_time": datetime(2026, 8, day, 9, 0),
                        "item_id": it, "value": -v if it == "Ioff" else v})
    return pl.DataFrame(rows)


def _db(tmp_path, frame=None, name="et.duckdb") -> str:
    p = tmp_path / f"{name}.parquet"
    (frame if frame is not None else _long()).write_parquet(p)
    dbp = tmp_path / name
    with db.Store(dbp) as store:
        db.pivot_and_load(store, [p])
    return str(dbp)


def _truth(dbp: str, items=ITEMS) -> pl.DataFrame:
    con = duckdb.connect(dbp, read_only=True)
    try:
        cols = ", ".join(f'"{c}"' for c in items)
        return con.execute(f"SELECT key_hash AS key, {cols} FROM et_data").pl()
    finally:
        con.close()


def _rf(absolute=("Ioff",)):
    from etreport.data import reformatter
    n = len(ITEMS)
    return reformatter.from_frame(pl.DataFrame({
        "CATEGORY": ["REAL"] * n, "ITEMID": [i.upper() for i in ITEMS],
        "ALIAS": ITEMS, "ABSOLUTE": ["Y" if a in absolute else None for a in ITEMS],
        "SCALE FACTOR": [None] * n, "ADDP FORM": [None] * n, "UNIT": [None] * n,
        "SPECLOW": [None] * n, "SPECHIGH": [None] * n, "TARGET": [None] * n}))


def _files(appdata) -> list[str]:
    folder = appdata / "workset"
    return sorted(p.name for p in folder.glob("*.parquet")) if folder.exists() else []


# ── 메모리에는 키만 ─────────────────────────────────────────────
def test_apply_keeps_only_keys_in_memory(tmp_path, appdata):
    st = AppState()
    loader.load_state(st, _db(tmp_path))
    assert st.data.columns == list(loader.RESERVED)
    assert set(st.workset.items) == set(ITEMS)
    assert st.workset.path.exists()


def test_frame_values_equal_the_db(tmp_path, appdata):
    dbp = _db(tmp_path)
    st = AppState()
    loader.load_state(st, dbp)
    got = loader.frame(st, ["Vt", "Gm"]).sort("key")
    want = _truth(dbp, ["Vt", "Gm"]).sort("key")
    assert got.height == want.height == 2 * 3 * 6
    assert got["Vt"].to_list() == want["Vt"].to_list()
    assert got["Gm"].to_list() == want["Gm"].to_list()
    assert "Id" not in got.columns                      # 부른 열만 붙는다


def test_frame_reapplies_absolute(tmp_path, appdata):
    """음수로 적재된 ABSOLUTE item은 읽을 때 다시 절대값(§10.2)."""
    st = AppState()
    st.rf = _rf()
    loader.load_state(st, _db(tmp_path))
    assert (loader.frame(st, ["Ioff"])["Ioff"] > 0).all()


def test_condition_filter_narrows_the_values_too(tmp_path, appdata):
    st = AppState()
    st.cond_filter = {"temp": "25"}
    loader.load_state(st, _db(tmp_path))
    got = loader.frame(st, ["Vt"])
    assert got.height == 2 * 3 * 3
    assert set(got["temp"].cast(pl.Utf8)) == {"25"}
    assert got["Vt"].null_count() == 0


def test_item_outside_the_workset_is_read_on_demand(tmp_path, appdata, monkeypatch):
    """item이 많은 DB는 템플릿 item만 작업 세트로 — 나머지는 고를 때 그 열만."""
    monkeypatch.setattr(loader, "LOAD_ALL_ITEMS", 1)
    dbp = _db(tmp_path)
    st = AppState()
    loader.load_state(st, dbp, items=["Vt"])
    assert st.workset.items == ["Vt"]
    got = loader.frame(st, ["Vt", "Gm"]).sort("key")
    assert got["Gm"].to_list() == _truth(dbp, ["Gm"]).sort("key")["Gm"].to_list()


def test_lot_selection_goes_into_the_workset(tmp_path, appdata):
    st = AppState()
    loader.load_state(st, _db(tmp_path), lots=["PA2"])
    assert set(st.data["lot"]) == {"PA2"}
    assert set(pl.read_parquet(st.workset.path, columns=["lot"])["lot"]) == {"PA2"}


# ── 파일 관리 ──────────────────────────────────────────────────
def test_same_selection_reuses_the_file(tmp_path, appdata):
    dbp = _db(tmp_path)
    st = AppState()
    loader.load_state(st, dbp)
    first = st.workset.path
    stamp = first.stat().st_mtime_ns
    os.utime(first, ns=(stamp - 10**9, stamp - 10**9))       # 한 번 쓴 뒤 시간이 지났다
    loader.load_state(st, dbp)
    assert st.workset.path == first
    assert _files(appdata) == [first.name]


def test_only_the_latest_sets_are_kept(tmp_path, appdata):
    dbp = _db(tmp_path)
    st = AppState()
    seen = []
    for lots in (["PA1"], ["PA2"], ["PA1", "PA2"]):
        loader.load_state(st, dbp, lots=lots)
        seen.append(st.workset.path.name)
    assert len(set(seen)) == 3
    assert _files(appdata) == sorted(seen[-workset.KEEP:])


def test_loading_more_data_makes_a_new_workset(tmp_path, appdata):
    dbp = _db(tmp_path)
    st = AppState()
    loader.load_state(st, dbp)
    before = st.workset.path
    p = tmp_path / "more.parquet"
    _long(lots=("PA3",)).write_parquet(p)
    with db.Store(dbp) as store:
        db.pivot_and_load(store, [p])
    loader.load_state(st, dbp)
    assert st.workset.path != before
    assert "PA3" in set(st.data["lot"])


def test_a_deleted_workset_is_rebuilt_on_read(tmp_path, appdata):
    """다른 창이 정리했거나 사용자가 지운 경우 — 읽을 때 다시 뜬다."""
    dbp = _db(tmp_path)
    st = AppState()
    loader.load_state(st, dbp)
    st.workset.path.unlink()
    got = loader.frame(st, ["Vt"]).sort("key")
    assert got["Vt"].to_list() == _truth(dbp, ["Vt"]).sort("key")["Vt"].to_list()


# ── 묶음 계산 = 한 번에 계산 ──────────────────────────────────
@pytest.fixture
def loaded(tmp_path, appdata, monkeypatch):
    st = AppState()
    st.rf = _rf()
    loader.load_state(st, _db(tmp_path))
    st.excluded = set(st.data["key"].head(3))
    monkeypatch.setattr(loader, "CHUNK_BYTES", 8)         # item 하나씩 나눠 읽게
    return st


def test_chunked_wafer_stats_equal_one_shot(loaded):
    from etreport.model.aggregate import wafer_stats, wafer_stats_of
    full = loader.frame(loaded, ITEMS)
    for agg in ("avg", "std", "med"):
        got = wafer_stats_of(loaded, ITEMS, agg)
        want = wafer_stats(full, loaded.hidden(), ITEMS, agg)
        assert got.values == want.values and got.excluded == want.excluded


def test_chunked_ref_values_equal_one_shot(loaded):
    from etreport.model.aggregate import ref_values, ref_values_of
    loaded.data = loaded.data.with_columns(
        pl.when(pl.col("lot") == "PA1").then(pl.lit("g1")).otherwise(pl.lit("g2"))
        .alias("gid"))
    full = loader.frame(loaded, ITEMS)
    assert ref_values_of(loaded, "g1", ITEMS, "avg") == \
        ref_values(full, loaded.hidden(), "g1", ITEMS, "avg")


def test_chunked_outlier_filter_equals_one_shot(loaded):
    from etreport.model import outliers
    full = loader.frame(loaded, ITEMS)
    cfg = outliers.TukeyConfig(enabled=True, k=0.1, scope="all")
    want = outliers.find(full, ITEMS, cfg)
    got = outliers.apply(loaded, cfg)
    assert want.points and set(got.points) == set(want.points)
    assert got.by_item == want.by_item and got.checked == want.checked


def test_coverage_reads_values_from_the_workset(loaded):
    from etreport.model import coverage
    full = loader.frame(loaded, ITEMS)
    got = coverage.build(loaded)
    want = coverage.build_frame(full, ITEMS)
    assert [(r.lot, r.items, r.wafers) for r in got.rows] == \
        [(r.lot, r.items, r.wafers) for r in want.rows]
    assert all(r.items == len(ITEMS) for r in got.rows)


def test_ppt_plot_data_carries_the_plot_items(loaded):
    """PPT 데이터 준비도 키 프레임 + 그 plot의 item 열 — 화면과 같은 함수."""
    from etreport.export.deckbuild import _plot_data
    from etreport.model.specs import PlotSpec
    data = _plot_data(loaded, "", PlotSpec(x="Vt", y="Gm"))
    sub = data[""]
    assert {"Vt", "Gm"} <= set(sub.columns) and "Id" not in sub.columns
    assert sub.height == loaded.data.height - 3          # 제외 3점


# ── long DB(손으로 만든 것)는 예전처럼 메모리에 ─────────────────
def test_long_db_stays_in_memory(tmp_path, appdata):
    p = tmp_path / "long.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""create table et_data(lot_id varchar, wafer varchar, die_x int,
        die_y int, item varchar, val double)""")
    con.execute("insert into et_data values ('PA1','01',1,1,'VT',0.5),"
                "('PA1','01',2,1,'VT',0.6)")
    con.close()
    st = AppState()
    loader.load_state(st, str(p))
    assert st.workset is None
    assert sorted(loader.frame(st, ["VT"])["VT"]) == [0.5, 0.6]
