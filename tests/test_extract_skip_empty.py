"""추출 성능 3건 — 빈 청크 건너뛰기 · 날짜 프로브 · 청크 폭(개발자 모드).

빈 날짜에도 무거운 GEN 조회를 던지고, 그 빈 결과로 리포메팅까지 돌던 것이
현장에서 가장 큰 낭비였다. 세 가지가 함께 그것을 없앤다.
  ① 행이 없는 청크는 리포메팅·적재에서 뺀다
  ② root_lot_id 조건이 있으면 **데이터가 있는 날짜만** 먼저 묻는다(SUM 클래스)
  ③ 청크 폭은 개발자 모드에서 1~N일로 바꾼다
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from etreport.config.catalog import Catalog, ColumnInfo
from etreport.config.settings import Condition, ExtractPreset
from etreport.data import extractor, pipeline
from etreport.data.querybuilder import build_date_probe_sql
from tests import factory

D1, D5 = date(2026, 8, 4), date(2026, 8, 8)


@pytest.fixture
def catalog() -> Catalog:
    cat = Catalog()
    cat.columns = [ColumnInfo("line_id", "STRING"),
                   ColumnInfo("root_lot_id", "STRING"),
                   ColumnInfo("tkout_time", "TIMESTAMP")]
    return cat


def _cond(lot: str = "") -> list[Condition]:
    rows = [Condition("line_id", "L1", required=True)]
    if lot:
        rows.append(Condition("root_lot_id", lot))
    return rows


# ── ② 날짜 프로브 ────────────────────────────────────────────
def test_probe_sql_asks_the_summary_class_only(catalog):
    """무거운 GEN이 아니라 요약(SUM) 행에서 날짜만 받는다."""
    sql = build_date_probe_sql(_cond("PA123"), D1, D5, catalog)

    assert "DISTINCT TO_DATE(tkout_time)" in sql
    assert "data_class = 'SUM'" in sql
    assert "root_lot_id = 'PA123'" in sql          # 조건은 그대로 실린다
    assert "item_id" not in sql                    # item 목록은 싣지 않는다


def test_probe_is_skipped_without_a_lot_condition(catalog, monkeypatch):
    """lot 조건이 없으면 예전 그대로 — 프로브 쿼리 자체가 없다."""
    calls: list[str] = []
    monkeypatch.setattr(extractor, "_fetch",
                        lambda sql: calls.append(sql) or pl.DataFrame())

    assert extractor.probe_days(_cond(), D1, D5, catalog) is None
    assert calls == []


@pytest.mark.parametrize("col", [
    pl.Series(["2026-08-05", "2026-08-07", "2026-08-05"]),
    pl.Series([date(2026, 8, 5), date(2026, 8, 7), date(2026, 8, 5)]),
])
def test_probe_reads_dates_in_any_shape(catalog, monkeypatch, col):
    """Impala가 문자열로 주든 날짜로 주든 같은 날짜 목록이 된다."""
    monkeypatch.setattr(extractor, "_fetch",
                        lambda sql: pl.DataFrame({"tkout_date": col}))

    assert extractor.probe_days(_cond("PA123"), D1, D5, catalog) == [
        date(2026, 8, 5), date(2026, 8, 7)]


def test_probe_failure_falls_back_to_the_whole_period(catalog, monkeypatch):
    """프로브는 최적화일 뿐 — 실패했다고 추출을 막지 않는다."""
    def boom(_sql):
        raise RuntimeError("bdq 없음")

    monkeypatch.setattr(extractor, "_fetch", boom)
    assert extractor.probe_days(_cond("PA123"), D1, D5, catalog) is None


def test_only_the_probed_days_become_chunks():
    """★ 5일 기간이라도 데이터가 있는 이틀만 조회한다."""
    days = [date(2026, 8, 5), date(2026, 8, 7)]
    chunks = extractor.plan_chunks(D1, D5, 1, days)

    assert [(c.d_from, c.d_to) for c in chunks] == [(d, d) for d in days]


def test_empty_probe_means_no_chunks():
    assert extractor.plan_chunks(D1, D5, 1, []) == []


# ── ③ 청크 폭 ────────────────────────────────────────────────
def test_chunk_width_groups_consecutive_days():
    """개발자 모드에서 2·3일로 늘리면 그만큼 한 쿼리로 묶인다."""
    assert [(c.d_from.day, c.d_to.day) for c in extractor.plan_chunks(D1, D5, 3)] \
        == [(4, 6), (7, 8)]
    assert [(c.d_from.day, c.d_to.day) for c in extractor.plan_chunks(D1, D5, 2)] \
        == [(4, 5), (6, 7), (8, 8)]


def test_default_width_is_unchanged():
    """기본값은 글자 하나까지 예전과 같다 — 하루가 청크 하나."""
    chunks = extractor.plan_chunks(D1, D5)
    assert len(chunks) == 5
    assert all(c.d_from == c.d_to for c in chunks)


def test_chunk_width_never_merges_across_a_gap():
    """빈 날짜를 건너뛰어도 없는 날짜가 조회 구간에 끼면 안 된다."""
    days = [date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 8)]
    chunks = extractor.plan_chunks(D1, D5, 3, days)

    assert [(c.d_from.day, c.d_to.day) for c in chunks] == [(4, 5), (8, 8)]


def test_dev_dialog_needs_the_password_to_change_anything(qapp_or_skip):
    """잠긴 채로 [확인]을 눌러도 값이 바뀌지 않는다."""
    from etreport.ui.widgets.dev_dialog import DEV_PASSWORD, DevDialog

    p = ExtractPreset(name="t", chunk_days=1)
    dlg = DevDialog(p)
    try:
        dlg.sp_days.setValue(3)              # 잠김 상태에서는 비활성이지만
        dlg.accept()                          # 값을 억지로 바꿔도 반영 안 됨
        assert p.chunk_days == 1

        dlg = DevDialog(p)
        dlg.ed_pw.setText(DEV_PASSWORD)
        dlg._unlock()
        dlg.sp_days.setValue(3)
        dlg.accept()
        assert p.chunk_days == 3
    finally:
        dlg.deleteLater()


# ── ① 빈 청크 건너뛰기 ───────────────────────────────────────
@pytest.fixture
def qapp_or_skip():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    return QApplication.instance() or QApplication([])


def _run_pipeline(tmp_path, monkeypatch, fake_sheet, catalog, has_data):
    """리포메팅이 몇 번 돌았는지 세면서 파이프라인을 한 번 돌린다."""
    from etreport.data import reformatter

    rf = factory.make_reformatter(n_real=4, n_addp=1, seed=3)
    fake_sheet(factory.rules_frame(rf.rules))
    items = [r.itemid for r in rf.reals()]
    empty = factory.make_long(items, lots=1, wafers=1, chips=1, seed=3).clear()

    def fake_fetch(sql: str) -> pl.DataFrame:
        day = int(sql.split("tkout_time >= '")[1][8:10])
        if not has_data(day):
            return empty
        return factory.make_long(items, lots=1, wafers=1, chips=2, seed=3,
                                 start=date(2026, 8, day))

    monkeypatch.setattr(extractor, "_fetch", fake_fetch)
    seen: list[int] = []
    real_apply = reformatter.apply

    def counting_apply(rf_, src, **kw):
        seen.append(src.height)
        return real_apply(rf_, src, **kw)

    monkeypatch.setattr(reformatter, "apply", counting_apply)
    preset = ExtractPreset(name="t", db_path=str(tmp_path / "et.duckdb"),
                           reformatter_path=str(tmp_path / "rf.xlsx"),
                           conditions=_cond(), save_csv=False)
    return pipeline.run(preset, D1, D5, catalog), seen


def test_empty_chunks_never_reach_the_reformatter(
        tmp_path, monkeypatch, fake_sheet, catalog, appdata):
    """★ 5일 중 이틀만 데이터가 있으면 리포메팅도 두 번만 돈다."""
    res, seen = _run_pipeline(tmp_path, monkeypatch, fake_sheet, catalog,
                              lambda d: d in (5, 7))

    assert len(seen) == 2                    # 빈 청크 3개는 통째로 건너뛴다
    assert all(h > 0 for h in seen)
    assert res.rows > 0


def test_all_empty_is_a_clear_failure(
        tmp_path, monkeypatch, fake_sheet, catalog, appdata):
    """전부 비면 빈 DB를 만드는 대신 이유를 말한다."""
    with pytest.raises(RuntimeError, match="데이터가 없습니다"):
        _run_pipeline(tmp_path, monkeypatch, fake_sheet, catalog,
                      lambda _d: False)
