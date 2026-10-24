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


def _wide_db(tmp_path, rows=20000, items=300) -> str:
    path = tmp_path / "data.duckdb"
    db = duckdb.connect(str(path))
    cols = ", ".join(f"random() AS i{n}" for n in range(items))
    db.execute(f"create table et_data as select range AS k, {cols} from range({rows})")
    db.close()
    return str(path)


def test_sbdf_is_not_gated_by_memory_estimate(tmp_path, monkeypatch):
    """★ SBDF는 추정으로 미리 거부하지 않는다 — 가용 메모리가 바닥이라 읽혀도 쓴다.

    예전 사전 점검(가용 메모리 × 0.5 ÷ 3)이 예전 버전에서 잘 되던 수 GB
    내보내기까지 막았다. 모자라면 실제 할당에서 실패할 뿐이다.
    """
    from etreport.data import extractor
    monkeypatch.setattr(extractor, "available_memory_bytes", lambda: 10)
    sbdf, labels = Mock(), []
    n = exporting.export_sbdf(_wide_db(tmp_path), "select * from et_data;",
                              "out.sbdf", sbdf, report=labels.append)
    assert n == 20000
    assert sbdf.export_data.call_args[0][0].shape == (20000, 301)
    assert "20,000행 × 301열" in labels[-1]          # 단계가 보인다


def test_wide_db_export_is_not_blocked_by_read_cap(tmp_path, monkeypatch):
    """★ 열이 많은 DB도 SQL 창 미리보기·CSV/parquet·SBDF가 읽기 상한에 막히지 않는다.

    DuckDB 스캔은 열마다 블록을 고정해 두어서(열 하나 ≈ 0.5MB) item 5,000열이면
    결과 크기와 상관없이 2.5GB가 먼저 든다. 2~6GB 상한 아래에서는 미리보기 200행도
    시작하자마자 `failed to pin block`으로 떨어졌다 — 같은 비율을 작게 만든다.
    """
    from etreport.data import loader
    from etreport.ui.widgets.sql_dialog import preview_query
    db = _wide_db(tmp_path, rows=40000, items=200)
    monkeypatch.setattr(loader, "duckdb_memory_limit", lambda total=None: "16MB")
    sql = "select * from et_data"
    assert preview_query(db, sql)[0].shape == (200, 201)
    exporting.copy_to(db, sql, str(tmp_path / "q.parquet"), "parquet")
    assert exporting.export_sbdf(db, sql, "q.sbdf", Mock()) == 40000


def test_sbdf_memory_failure_points_to_streaming_formats(tmp_path):
    sbdf = Mock()
    sbdf.export_data.side_effect = MemoryError("boom")
    with pytest.raises(MemoryError, match="CSV나 parquet"):
        exporting.export_sbdf(_wide_db(tmp_path, 10, 2), "select * from et_data",
                              "out.sbdf", sbdf)


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
