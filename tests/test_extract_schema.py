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
from etreport.data import extractor

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
