"""§4.2 청크 = 기간 × item 그룹 · §4.3 진행 표시 · §10.10 SQL 미리보기.

사양의 예시 그대로: item 24,180개 → 3그룹, 기간 7일 → **21청크를 4워커가**
나눠 문다. 그룹이 여러 개인데 날짜만 병렬로 돌리면 실질 병렬도가 그룹 수만큼
떨어진다(정확성 문제는 아니지만 추출이 가장 비싼 단계다).

미리보기는 목록을 만들지 않는다 — 24,180개를 문자열로 펴면 43만 자가 되어
조건을 고칠 때마다 다시 그린다.
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from etreport.config.catalog import Catalog, ColumnInfo
from etreport.config.settings import Condition
from etreport.data import extractor
from etreport.data.querybuilder import (
    ITEM_ID_CHUNK,
    build_extract_sql,
    build_preview_sql,
)

D1, D7 = date(2026, 8, 4), date(2026, 8, 10)


@pytest.fixture
def catalog() -> Catalog:
    cat = Catalog()
    cat.columns = [ColumnInfo("line_id", "STRING"),
                   ColumnInfo("tkout_time", "TIMESTAMP")]
    return cat


def _cond() -> list[Condition]:
    return [Condition("line_id", "L1", required=True)]


def _fake_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "root_lot_id": ["L1"], "wafer_id": ["W1"], "chip_x_pos": [1],
        "chip_y_pos": [1], "temperature": [25.0], "step_id": ["M2"],
        "step_seq": [1], "total_site_cnt": [9], "tkout_time": [None],
        "item_id": ["X"], "et_value": [1.0]})


# ── 그룹 나누기 ──────────────────────────────────────────────
@pytest.mark.parametrize("n,expect", [(0, 1), (1, 1), (9999, 1), (10000, 2),
                                      (24180, 3), (19998, 2)])
def test_item_groups_respect_the_limit(n, expect):
    groups = extractor.item_groups([f"I{i}" for i in range(n)] or None)

    assert len(groups) == expect
    for g in groups:
        assert g is None or len(g) <= ITEM_ID_CHUNK


def test_no_filter_is_one_group_of_none():
    assert extractor.item_groups(None) == [None]


# ── 기간 × 그룹 ──────────────────────────────────────────────
def test_units_are_the_product_of_days_and_groups():
    """★ 사양 예시 — item 24,180개 · 7일 → 21청크."""
    units = extractor.plan_units(D1, D7, [f"I{i}" for i in range(24180)])

    assert len(units) == 21
    assert {u.n_groups for u in units} == {3}
    assert len({(u.chunk.d_from, u.group) for u in units}) == 21
    assert all(len(u.item_ids) <= ITEM_ID_CHUNK for u in units)


def test_unit_labels_show_the_item_group():
    """§4.3 진행 로그 — '08-04 item 2/3'."""
    units = extractor.plan_units(D1, D7, [f"I{i}" for i in range(24180)])

    assert units[0].label() == "08-04 item 1/3"
    assert units[1].label() == "08-04 item 2/3"
    assert units[-1].label() == "08-10 item 3/3"


def test_single_group_label_is_just_the_day():
    units = extractor.plan_units(D1, D1, [f"I{i}" for i in range(10)])
    assert units[0].label() == "08-04"


# ── 실행 ─────────────────────────────────────────────────────
def test_every_unit_is_submitted_and_reported(tmp_path, monkeypatch, catalog):
    """★ 병렬 단위·진행률·파일 이름이 모두 (기간 × 그룹)을 따라간다."""
    monkeypatch.setattr(extractor, "_fetch", lambda sql: _fake_frame())
    seen: list[tuple[int, int, str]] = []

    files = extractor.extract_to_parquet(
        _cond(), D1, date(2026, 8, 5), catalog, tmp_path,
        on_progress=lambda d, t, label: seen.append((d, t, label)),
        item_ids=[f"I{i}" for i in range(24180)])

    assert len(files) == 2 * 3                       # 이틀 × 3그룹
    assert [t for _d, t, _l in seen] == [6] * 6      # 전체 개수는 곱
    assert sorted(d for d, _t, _l in seen) == [1, 2, 3, 4, 5, 6]
    assert {label.split()[-1] for _d, _t, label in seen} == {"1/3", "2/3", "3/3"}
    # 파일 이름에 그룹 번호 — 같은 날짜끼리 덮어쓰지 않는다(§4.2)
    assert {f.stem.split("_")[-1] for f in files} == {"g1", "g2", "g3"}


def test_small_item_list_keeps_plain_file_names(tmp_path, monkeypatch, catalog):
    monkeypatch.setattr(extractor, "_fetch", lambda sql: _fake_frame())

    files = extractor.extract_to_parquet(
        _cond(), D1, D1, catalog, tmp_path, item_ids=["A", "B"])

    assert len(files) == 1
    assert not files[0].stem.endswith(("g1", "g2"))


def test_each_query_stays_under_the_limit(tmp_path, monkeypatch, catalog):
    """쿼리 하나에 들어가는 item 수가 상한을 넘지 않는다(§4.1)."""
    counts: list[int] = []

    def fake_fetch(sql: str) -> pl.DataFrame:
        counts.append(sql.count("'I"))
        return _fake_frame()

    monkeypatch.setattr(extractor, "_fetch", fake_fetch)
    extractor.extract_to_parquet(
        _cond(), D1, D1, catalog, tmp_path,
        item_ids=[f"I{i}" for i in range(24180)])

    assert len(counts) == 3
    assert max(counts) <= ITEM_ID_CHUNK
    assert sum(counts) == 24180                      # 하나도 빠뜨리지 않는다


# ── 미리보기 (§10.10) ────────────────────────────────────────
def test_preview_does_not_materialise_the_item_list(catalog):
    """★ 24,180개를 문자열로 펴지 않는다 — 조건을 고칠 때마다 다시 그린다."""
    items = [f"ET_PARAM_{i:05d}" for i in range(24180)]

    preview = build_preview_sql(_cond(), D1, D7, catalog, item_ids=items)
    real = build_extract_sql(_cond(), D1, D7, catalog, item_ids=items)

    assert len(preview) < 1000                       # 실행용은 40만 자가 넘는다
    assert len(real) > 100_000
    assert "item_id IN ( … 24,180개 … )" in preview
    assert "3개 그룹" in preview                      # 몇 번 쿼리하는지 알려 준다
    assert "ET_PARAM_10000" not in preview           # 목록은 없다
    assert "ET_PARAM_10000" in real                  # 실행용에는 전부 있다


def test_preview_keeps_the_real_conditions(catalog):
    """미리보기라고 조건이 달라지면 안 된다 — WHERE 앞부분은 실행용과 같다."""
    preview = build_preview_sql(_cond(), D1, D7, catalog, item_ids=["A"])
    real = build_extract_sql(_cond(), D1, D7, catalog, item_ids=["A"])

    for line in ("line_id = 'L1'", "tkout_time >= '2026-08-04 00:00:00'",
                 "tkout_time <  '2026-08-11 00:00:00'"):
        assert line in preview and line in real


def test_preview_without_items_is_plain_sql(catalog):
    sql = build_preview_sql(_cond(), D1, D7, catalog)

    assert "item_id" not in sql.split("WHERE")[1]
    assert "--" not in sql
