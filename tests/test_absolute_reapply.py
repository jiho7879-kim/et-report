"""§10.2 — ABSOLUTE가 안 먹는 것처럼 보이는 문제.

절대값을 **추출 시점에만** 적용하면, 이미 음수로 적재된 DB는 리포메터를 고쳐도
그대로다. 그래서 분석 로딩·[적용] 시점에도 건다. 절대값은 멱등(|x| 두 번 =
|x|)이라 안전하지만, **스케일은 멱등이 아니므로 재적용하면 안 된다** — 이
두 가지를 함께 못 박는다.
"""
from __future__ import annotations

from datetime import datetime

import polars as pl
import pytest

from etreport.data import db, loader
from etreport.data.reformatter import Reformatter, Rule
from etreport.model.state import AppState

BASE = {"line_id": "L1", "root_lot_id": "PA100", "wafer_id": "01",
        "chip_x_pos": 3, "chip_y_pos": 4, "temperature": 25.0,
        "step_id": "M2", "step_seq": 1, "total_site_cnt": 9,
        "tkout_time": datetime(2026, 8, 4, 9, 0)}


def _rule(alias: str, absolute: bool, scale: float = 1.0, row: int = 2) -> Rule:
    return Rule(category="REAL", itemid=f"P{row}", alias=alias,
                absolute=absolute, scale=scale, formula="", unit="",
                speclow=None, spechigh=None, target=None, row=row)


def _db_with_negatives(tmp_path) -> str:
    """음수가 그대로 적재된 DB — 리포메터를 고치기 전에 쌓인 기존 DB를 흉내낸다."""
    long = pl.DataFrame([
        {**BASE, "chip_x_pos": 1, "item_id": "Ioff", "et_value": -1.5e-9},
        {**BASE, "chip_x_pos": 1, "item_id": "Vt", "et_value": -0.42},
        {**BASE, "chip_x_pos": 2, "item_id": "Ioff", "et_value": -2.5e-9},
        {**BASE, "chip_x_pos": 2, "item_id": "Vt", "et_value": 0.44},
    ])
    p = tmp_path / "neg.parquet"
    long.write_parquet(p)
    dbp = tmp_path / "neg.duckdb"
    store = db.Store(dbp)
    try:
        db.pivot_and_load(store, [p])
    finally:
        store.close()
    return str(dbp)


def _load(dbp: str, rules: list[Rule]) -> AppState:
    st = AppState()
    st.rf = Reformatter(rules=rules)
    loader.load_state(st, dbp)
    return st


def test_absolute_is_reapplied_to_already_loaded_negatives(tmp_path, appdata):
    """★ 음수로 적재된 기존 DB도 리포메터를 고치면 절대값이 먹어야 한다."""
    dbp = _db_with_negatives(tmp_path)

    st = _load(dbp, [_rule("Ioff", absolute=True)])

    assert sorted(st.data["Ioff"]) == pytest.approx([1.5e-9, 2.5e-9])
    # DB 원본은 그대로 — 분석은 읽기 전용이다
    con = loader.open_readonly(dbp)
    assert min(r[0] for r in con.execute("select Ioff from et_data").fetchall()) < 0
    con.close()


def test_absolute_false_items_keep_their_sign(tmp_path, appdata):
    """ABSOLUTE가 아닌 item은 음수 그대로 — 모든 값을 뒤집지 않는다."""
    st = _load(_db_with_negatives(tmp_path),
               [_rule("Ioff", absolute=True), _rule("Vt", absolute=False, row=3)])

    assert sorted(st.data["Ioff"]) == pytest.approx([1.5e-9, 2.5e-9])
    assert sorted(st.data["Vt"]) == pytest.approx([-0.42, 0.44])


def test_scale_is_never_reapplied(tmp_path, appdata):
    """스케일은 멱등이 아니다 — 로딩에서 다시 곱하면 배율만큼 어긋난다."""
    st = _load(_db_with_negatives(tmp_path),
               [_rule("Ioff", absolute=True, scale=1000.0)])

    assert sorted(st.data["Ioff"]) == pytest.approx([1.5e-9, 2.5e-9])


def test_reapplying_is_idempotent(tmp_path, appdata):
    """|x|를 몇 번 걸어도 값이 같다 — [적용]을 여러 번 눌러도 안전하다."""
    dbp = _db_with_negatives(tmp_path)
    rules = [_rule("Ioff", absolute=True)]

    first = _load(dbp, rules).data.sort("key")["Ioff"].to_list()
    second = _load(dbp, rules).data.sort("key")["Ioff"].to_list()

    assert first == pytest.approx(second)


def test_no_reformatter_changes_nothing(tmp_path, appdata):
    """리포메터 없이 DB만 열어 보는 흐름 — 값을 건드리지 않는다."""
    st = _load(_db_with_negatives(tmp_path), [])

    assert sorted(st.data["Ioff"]) == pytest.approx([-2.5e-9, -1.5e-9])


def test_summary_reports_how_many_items_were_absolutised(tmp_path, appdata):
    """도크 요약에 보여야 한다 — 값이 왜 바뀌었는지 화면에서 알 수 있게."""
    st = AppState()
    st.rf = Reformatter(rules=[_rule("Ioff", absolute=True)])
    summary = loader.load_state(st, _db_with_negatives(tmp_path))

    assert "절대값 1" in summary


def test_apply_absolute_skips_reserved_and_missing(tmp_path, appdata):
    """예약 컬럼(key/lot/wafer/gid)이나 DB에 없는 alias는 건너뛴다."""
    df = pl.DataFrame({"key": ["k1"], "lot": ["PA100"], "wafer": ["01"],
                       "gid": [""], "Ioff": [-1.0]})
    rules = [_rule("lot", absolute=True), _rule("없는item", absolute=True, row=3),
             _rule("Ioff", absolute=True, row=4)]

    out, n = loader.apply_absolute(df, Reformatter(rules=rules))

    assert n == 1
    assert out["Ioff"][0] == pytest.approx(1.0)
    assert out["lot"][0] == "PA100"
