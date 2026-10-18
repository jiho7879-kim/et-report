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


def test_sbdf_small_result_and_trailing_semicolon(con):
    assert exporting.preflight_sbdf(con, "select 1 as n, '한글' as s;") == 1


def test_sbdf_rejects_wide_result(con, monkeypatch):
    monkeypatch.setattr(exporting, "sbdf_budget", lambda: 3 * 8 * 3 - 1)
    with pytest.raises(ValueError, match="메모리 부족"):
        exporting.preflight_sbdf(con, "select 1 a, 2 b, 3 c")


def test_sbdf_counts_string_payload(con, monkeypatch):
    monkeypatch.setattr(exporting, "sbdf_budget", lambda: 2048)
    with pytest.raises(ValueError, match="메모리 부족"):
        exporting.preflight_sbdf(con, "select repeat('한', 1000) as text")


def test_sbdf_respects_available_memory(con, monkeypatch):
    from etreport.data import extractor
    monkeypatch.setattr(extractor, "available_memory_bytes", lambda: 10)
    with pytest.raises(ValueError, match="메모리 부족"):
        exporting.preflight_sbdf(con, "select 1")


def test_sbdf_accepts_realistic_wide_result(con, monkeypatch):
    """★ 20만 행 × item 300열(숫자)은 4GB 여유에서 통과한다.

    예전 추정(셀당 512바이트·1GB 고정)은 이걸 30GB로 보고 거부했다 — SQL 창의
    SBDF 저장이 사실상 막혀 있던 원인.
    """
    from etreport.data import extractor
    monkeypatch.setattr(extractor, "available_memory_bytes", lambda: 4 << 30)
    cols = ", ".join(f"random() AS i{n}" for n in range(300))
    sql = f"select range AS k, 'L1' AS lot, {cols} from range(200000)"
    assert exporting.preflight_sbdf(con, sql) == 200_000


def test_sbdf_budget_falls_back_to_total_memory(monkeypatch):
    from etreport.data import extractor, loader
    monkeypatch.setattr(extractor, "available_memory_bytes", lambda: None)
    monkeypatch.setattr(loader, "total_memory_bytes", lambda: 16 << 30)
    assert exporting.sbdf_budget() == 4 << 30


def test_sbdf_rejects_nested_columns(con):
    with pytest.raises(ValueError, match="복합형"):
        exporting.preflight_sbdf(con, "select [1, 2] as nested")


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


def test_sql_sbdf_guard_precedes_dataframe(monkeypatch):
    from etreport.ui.widgets import sql_dialog
    db = Mock()
    db.__enter__ = Mock(return_value=db)
    db.__exit__ = Mock(return_value=False)
    from etreport.data import loader
    monkeypatch.setattr(loader, "readonly_query", lambda _: db)
    monkeypatch.setattr(sql_dialog, "import_sbdf", lambda: Mock())
    monkeypatch.setattr(sql_dialog.QFileDialog, "getSaveFileName",
                        lambda *a: ("result.sbdf", ""))
    error = Mock()
    monkeypatch.setattr(sql_dialog.QMessageBox, "critical", error)
    guard = Mock(side_effect=ValueError("unsafe"))
    monkeypatch.setattr(sql_dialog, "preflight_sbdf", guard)
    ws = SimpleNamespace(_ready=lambda: True, db_path="test.duckdb",
                         sql_text=lambda: "select * from et_data")
    sql_dialog.SqlExportDialog._save_sbdf(ws)
    guard.assert_called_once()
    db.execute.assert_not_called()
    error.assert_called_once()


def test_scheduled_sbdf_guard_precedes_dataframe(tmp_path, monkeypatch):
    from etreport.data import loader
    db = Mock()
    db.__enter__ = Mock(return_value=db)
    db.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(loader, "readonly_query", lambda _: db)
    sbdf = Mock()
    monkeypatch.setattr(exporting, "import_sbdf", lambda: sbdf)
    monkeypatch.setattr(exporting, "preflight_sbdf",
                        Mock(side_effect=ValueError("unsafe")))
    preset = SimpleNamespace(db_path=str(tmp_path / "data.duckdb"),
                             out_dir=str(tmp_path), save_csv=False, save_sbdf=True)
    assert exporting.save_wide(preset) == []
    db.execute.assert_not_called()
    sbdf.export_data.assert_not_called()


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_streaming_exports_do_not_use_sbdf_guard(tmp_path, monkeypatch, fmt):
    path = tmp_path / "data.duckdb"
    db = duckdb.connect(str(path))
    db.execute("create table et_data as select * from range(5)")
    db.close()
    monkeypatch.setattr(exporting, "preflight_sbdf",
                        Mock(side_effect=AssertionError("not a whole-frame export")))
    target = tmp_path / f"data.{fmt}"
    assert exporting.copy_to(str(path), "select * from et_data", str(target), fmt) == str(target)
    assert target.stat().st_size > 0
