"""Post-extraction handoff and whole-frame export safety regressions."""
from types import SimpleNamespace
from unittest.mock import Mock

import duckdb
import pytest

from etreport.data import exporting


@pytest.fixture
def con():
    db = duckdb.connect()
    yield db
    db.close()


def test_sbdf_fetch_is_not_gated_by_memory_estimate(tmp_path, monkeypatch):
    """★ SBDF는 추정으로 미리 거부하지 않는다 — 가용 메모리가 바닥이라 읽혀도 받는다.

    예전 사전 점검(가용 메모리 × 0.5 ÷ 3)이 예전 버전에서 잘 되던 수 GB
    내보내기까지 막았다. 모자라면 실제 할당에서 실패할 뿐이다.
    """
    from etreport.data import extractor
    monkeypatch.setattr(extractor, "available_memory_bytes", lambda: 10)
    path = tmp_path / "data.duckdb"
    db = duckdb.connect(str(path))
    cols = ", ".join(f"random() AS i{n}" for n in range(300))
    db.execute(f"create table et_data as select range AS k, {cols} from range(20000)")
    db.close()
    full = exporting.fetch_frame(str(path), "select * from et_data;")
    assert full.shape == (20000, 301)


def test_handoff_populates_lots_without_applying():
    from etreport.ui.analysis_ws import AnalysisWorkspace
    config = SimpleNamespace(db_path="")
    ws = SimpleNamespace(cfg=lambda: config, _load_lots=Mock(),
                         _mark_unapplied=Mock(), apply_config=Mock())
    AnalysisWorkspace.connect_db(ws, "new.duckdb")
    assert config.db_path == "new.duckdb"
    ws._load_lots.assert_called_once_with("new.duckdb")
    ws._mark_unapplied.assert_called_once()
    ws.apply_config.assert_not_called()


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_streaming_exports_write_files(tmp_path, fmt):
    path = tmp_path / "data.duckdb"
    db = duckdb.connect(str(path))
    db.execute("create table et_data as select * from range(5)")
    db.close()
    target = tmp_path / f"data.{fmt}"
    assert exporting.copy_to(str(path), "select * from et_data", str(target), fmt) == str(target)
    assert target.stat().st_size > 0
