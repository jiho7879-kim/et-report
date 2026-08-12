"""extractor — item_id 9999 초과 시 별도 parquet 청크로 분리."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from etreport.config.catalog import Catalog
from etreport.config.settings import Condition
from etreport.data import extractor


def test_extract_chunks_by_item_id(tmp_path, monkeypatch):
    # 가짜 bdq 결과 — KEY 컬럼 일부 + item_id/et_value
    fake = pl.DataFrame({
        "root_lot_id": ["L1"], "wafer_id": ["W1"],
        "chip_x_pos": [1], "chip_y_pos": [1], "temperature": [25.0],
        "step_id": ["M2"], "step_seq": [1], "total_site_cnt": [9],
        "tkout_time": [None], "item_id": ["X"], "et_value": [1.0],
    })

    def fake_fetch(sql):
        return fake

    monkeypatch.setattr(extractor, "_fetch", fake_fetch)

    ids = [f"I{i}" for i in range(15000)]     # → 2개 item 청크
    files = extractor.extract_to_parquet(
        [Condition(col="line_id", val="KFBK", required=True)],
        date(2026, 1, 1), date(2026, 1, 1), Catalog(), tmp_path,
        item_ids=ids)

    # 날짜 청크 1개 × item 청크 2개 = 2 parquet
    assert len(files) == 2
    for f in files:
        assert Path(f).exists()


def test_extract_single_chunk_when_small(tmp_path, monkeypatch):
    fake = pl.DataFrame({
        "root_lot_id": ["L1"], "wafer_id": ["W1"],
        "chip_x_pos": [1], "chip_y_pos": [1], "temperature": [25.0],
        "step_id": ["M2"], "step_seq": [1], "total_site_cnt": [9],
        "tkout_time": [None], "item_id": ["X"], "et_value": [1.0],
    })
    monkeypatch.setattr(extractor, "_fetch", lambda sql: fake)

    ids = [f"I{i}" for i in range(500)]        # 9999 미만 → 1 청크
    files = extractor.extract_to_parquet(
        [Condition(col="line_id", val="KFBK", required=True)],
        date(2026, 1, 1), date(2026, 1, 1), Catalog(), tmp_path,
        item_ids=ids)
    assert len(files) == 1


def test_extract_no_filter_unchanged(tmp_path, monkeypatch):
    fake = pl.DataFrame({
        "root_lot_id": ["L1"], "wafer_id": ["W1"],
        "chip_x_pos": [1], "chip_y_pos": [1], "temperature": [25.0],
        "step_id": ["M2"], "step_seq": [1], "total_site_cnt": [9],
        "tkout_time": [None], "item_id": ["X"], "et_value": [1.0],
    })
    monkeypatch.setattr(extractor, "_fetch", lambda sql: fake)

    files = extractor.extract_to_parquet(
        [Condition(col="line_id", val="KFBK", required=True)],
        date(2026, 1, 1), date(2026, 1, 1), Catalog(), tmp_path)
    assert len(files) == 1
