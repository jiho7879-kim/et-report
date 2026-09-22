"""§10.4 — bdq 결과를 long 스키마로 맞출 때의 두 함정.

1) bdq/pandas를 거치면 반복 문자열이 **Categorical**로 온다. polars는
   Categorical → 숫자 직접 캐스팅을 막는다(`cannot cast categorical types`).
2) 같은 함수에서 **문자열 → Datetime은 `cast`가 아니라 파싱**이어야 한다.
   cast는 예외도 없이 전부 null을 만든다 — tkout_time이 null이 되면 key_hash가
   뭉쳐 서로 다른 측정이 중복으로 지워지고, retest 최신 판정도 무의미해진다.

여기서 못 잡으면 사내 PC에서 "추출은 됐는데 값이 이상하다"로만 보인다.
"""
from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from etreport.config.catalog import Catalog, ColumnInfo
from etreport.config.settings import Condition
from etreport.data import db, extractor, loader
from etreport.model.state import AppState

TARGET = extractor._target_dtypes()


def _bdq_like(**over) -> pl.DataFrame:
    """bdq가 돌려주는 모양 — 반복 문자열은 Categorical, 시각은 문자열."""
    base = {
        "line_id": pl.Series(["L1", "L1"], dtype=pl.Categorical),
        "root_lot_id": pl.Series(["PA100", "PA100"], dtype=pl.Categorical),
        "wafer_id": pl.Series(["01", "01"], dtype=pl.Categorical),
        "chip_x_pos": [3, 3],
        "chip_y_pos": [4, 4],
        "temperature": [25.0, 25.0],
        "step_id": pl.Series(["M2", "M2"], dtype=pl.Categorical),
        "step_seq": [1, 2],
        "total_site_cnt": pl.Series(["9", "9"], dtype=pl.Categorical),
        "tkout_time": ["2026-08-04 09:00:00", "2026-08-04 09:05:00"],
        "item_id": pl.Series(["Vt", "Ioff"], dtype=pl.Categorical),
        "et_value": [0.42, -1.5e-9],
    }
    base.update(over)
    return pl.DataFrame(base)


def test_string_tkout_time_is_parsed_not_cast():
    """★ 문자열 시각이 값을 잃지 않고 Datetime이 된다 (cast였다면 전부 null)."""
    out = extractor.normalize_schema(_bdq_like())

    assert out.schema["tkout_time"] == TARGET["tkout_time"]
    assert out["tkout_time"].null_count() == 0
    assert out["tkout_time"].to_list() == [datetime(2026, 8, 4, 9, 0),
                                           datetime(2026, 8, 4, 9, 5)]


def test_categorical_numeric_column_survives():
    """Categorical로 온 숫자 컬럼도 Utf8을 거쳐 숫자가 된다 (직접 캐스팅은 막힌다)."""
    out = extractor.normalize_schema(_bdq_like())

    assert out.schema["total_site_cnt"] == TARGET["total_site_cnt"]
    assert out["total_site_cnt"].to_list() == [9, 9]
    assert out.schema["step_id"] == pl.Utf8
    assert out["step_id"].to_list() == ["M2", "M2"]


def test_already_typed_frame_is_left_alone():
    """이미 제대로 된 타입이면 다시 만지지 않는다 (재파싱으로 깨지지 않게)."""
    src = _bdq_like(tkout_time=[datetime(2026, 8, 4, 9, 0),
                                datetime(2026, 8, 4, 9, 5)])
    out = extractor.normalize_schema(src)
    assert out["tkout_time"].null_count() == 0
    assert out["et_value"].to_list() == pytest.approx([0.42, -1.5e-9])


def test_unparseable_time_becomes_null_without_killing_the_chunk():
    """이상한 시각 문자열은 그 값만 null — 청크 전체가 예외로 죽지 않는다."""
    out = extractor.normalize_schema(
        _bdq_like(tkout_time=["2026-08-04 09:00:00", "알 수 없음"]))
    assert out["tkout_time"].null_count() == 1
    assert out.height == 2


def test_missing_columns_are_filled_so_chunks_share_a_schema():
    """빠진 컬럼은 null로 채워 청크 parquet 스키마를 고정한다."""
    src = _bdq_like().drop(["temperature", "step_seq"])
    out = extractor.normalize_schema(src)

    assert out.columns == list(TARGET)
    assert out.schema["temperature"] == TARGET["temperature"]
    assert out["step_seq"].null_count() == 2


def test_getdata_uppercase_decimal_step_seq_survives_to_duckdb_and_analysis(
        tmp_path, monkeypatch, appdata):
    """실제 getData 표기(`STEP_SEQ='1.0'`)는 NULL seq로 바뀌면 안 된다.

    DuckDB에는 seq별 두 물리 행이 남아야 하고, 분석용 프레임에서만 seq를
    무시해 서로 다른 item을 한 측정점으로 조합해야 한다. 이 조합이 깨지면
    summary와 scatter 모두 빈 값/점이 된다.
    """
    raw = pl.DataFrame({
        "LINE_ID": ["L1", "L1"],
        "ROOT_LOT_ID": ["PA100", "PA100"],
        "WAFER_ID": ["01", "01"],
        "CHIP_X_POS": ["3.0", "3.0"],
        "CHIP_Y_POS": ["4.0", "4.0"],
        "TEMPERATURE": ["25.0", "25.0"],
        "STEP_ID": ["M2", "M2"],
        "STEP_SEQ": ["1.0", "2.0"],
        "TOTAL_SITE_CNT": ["9.0", "9.0"],
        "TKOUT_TIME": ["2026-08-04 09:00:00", "2026-08-04 09:05:00"],
        "ITEM_ID": ["Vt", "Ioff"],
        "ET_VALUE": ["0.42", "1.5e-9"],
    })
    monkeypatch.setattr(extractor, "_fetch", lambda _sql: raw)
    cat = Catalog()
    cat.columns = [ColumnInfo("line_id", "STRING"),
                   ColumnInfo("tkout_time", "TIMESTAMP")]
    files = extractor.extract_to_parquet(
        [Condition("line_id", "L1", required=True)],
        date(2026, 8, 4), date(2026, 8, 4), cat, tmp_path)

    staged = pl.read_parquet(files[0])
    assert staged["step_seq"].to_list() == [1, 2]
    assert staged["step_seq"].null_count() == 0

    db_path = tmp_path / "et.duckdb"
    store = db.Store(db_path)
    try:
        assert db.pivot_and_load(store, files) == 2
    finally:
        store.close()

    con = __import__("duckdb").connect(str(db_path), read_only=True)
    try:
        stored = con.execute(
            'SELECT step_seq, "Vt", "Ioff" FROM et_data ORDER BY step_seq').fetchall()
    finally:
        con.close()
    assert stored == [(1, 0.42, None), (2, None, 1.5e-9)]

    state = AppState()
    loader.load_state(state, str(db_path))
    assert state.data.height == 1                 # 분석에서만 step_seq를 무시
    point = state.data.row(0, named=True)
    assert point["Vt"] == pytest.approx(0.42)
    assert point["Ioff"] == pytest.approx(1.5e-9)


def test_extract_writes_chunks_that_scan_together(tmp_path, monkeypatch):
    """추출 경로 전체 — 청크마다 모양이 달라도 한 번에 scan_parquet 된다."""
    frames = [_bdq_like(),                                  # 정상
              _bdq_like().drop(["temperature"])]            # 컬럼 하나 빠짐
    seen: list[str] = []

    def fake_fetch(sql: str) -> pl.DataFrame:
        seen.append(sql)
        return frames[len(seen) - 1]

    monkeypatch.setattr(extractor, "_fetch", fake_fetch)
    cat = Catalog()
    cat.columns = [ColumnInfo("line_id", "STRING"),
                   ColumnInfo("tkout_time", "TIMESTAMP")]

    files = extractor.extract_to_parquet(
        [Condition("line_id", "L1", required=True)],
        date(2026, 8, 4), date(2026, 8, 5), cat, tmp_path)

    assert len(files) == 2
    got = pl.scan_parquet([str(f) for f in files]).collect()
    assert got.height == 4
    assert got.schema["tkout_time"] == TARGET["tkout_time"]
    assert got["tkout_time"].null_count() == 0
    assert dict(got.schema) == TARGET


# ── step_seq가 통째로 NULL이 되는 경로 ───────────────────────
def test_decimal_step_seq_survives_normalisation():
    """★ Decimal로 돌아온 step_seq를 NULL로 만들지 않는다.

    Impala DECIMAL이 pandas object로 오면 `pl.from_pandas` 폴백이 Decimal
    dtype을 만든다. 예전 코드는 Utf8만 거쳐 보냈기 때문에 그 열에 곧바로
    `cast(Int32)`가 걸려 **예외 없이 전부 NULL**이 됐다 — DuckDB의 step_seq가
    비고, retest 중복 제거 파티션이 무너져 seq가 다른 item이 사라졌다.
    """
    from decimal import Decimal

    df = pl.DataFrame({
        "root_lot_id": ["PA1", "PA1"],
        "wafer_id": ["01", "01"],
        "step_seq": pl.Series([Decimal("1"), Decimal("2")],
                              dtype=pl.Decimal(precision=8, scale=0)),
        "item_id": ["Vt", "Ioff"],
        "et_value": [0.4, 1e-9],
    })
    got = extractor.normalize_schema(df)
    assert got["step_seq"].to_list() == [1, 2]


def test_empty_key_column_is_logged(caplog):
    """값이 있었는데 NULL이 되면 **로그에 이름이 남는다** — 현장의 유일한 단서."""
    df = pl.DataFrame({"root_lot_id": ["PA1"], "step_seq": ["x"],
                       "item_id": ["Vt"], "et_value": [0.4]})
    with caplog.at_level("ERROR"):
        extractor.normalize_schema(df)
    assert any("step_seq" in r.getMessage() for r in caplog.records)
