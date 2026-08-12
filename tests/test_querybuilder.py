"""querybuilder — item_id 필터 청크 분리 + 프로브 SQL."""
from __future__ import annotations

from datetime import date

from etreport.config.catalog import Catalog
from etreport.config.settings import Condition
from etreport.data.querybuilder import (
    ITEM_ID_CHUNK,
    _item_in_clauses,
    build_extract_sql,
    build_item_probe_sql,
)


def _cat() -> Catalog:
    c = Catalog()
    c.columns = []
    return c


def _conds() -> list[Condition]:
    return [Condition(col="line_id", val="KFBK", required=True)]


def _sql(d_from=date(2026, 1, 1), d_to=date(2026, 1, 1), **kw):
    return build_extract_sql(_conds(), d_from, d_to, _cat(), **kw)


def test_item_in_clauses_single_chunk():
    ids = [f"I{i}" for i in range(ITEM_ID_CHUNK)]          # 9999
    clauses = _item_in_clauses(ids)
    assert len(clauses) == 1
    assert clauses[0].startswith("item_id IN (")
    assert "'I0'" in clauses[0]


def test_item_in_clauses_two_chunks_at_10000():
    ids = [f"I{i}" for i in range(10000)]
    clauses = _item_in_clauses(ids)
    assert len(clauses) == 2
    assert clauses[0].count("'") == 2 * 9999               # 첫 청크 9999
    assert clauses[1].count("'") == 2                       # 두 번째 1개


def test_item_in_clauses_remainder():
    ids = [f"I{i}" for i in range(25000)]
    clauses = _item_in_clauses(ids)
    assert len(clauses) == 3                                # 9999, 9999, 5002


def test_item_in_clauses_escapes_quote():
    clauses = _item_in_clauses(["A", "B'C"])
    assert clauses[0] == "item_id IN ('A', 'B''C')"


def test_build_includes_item_filter():
    sql = _sql(item_ids=["A", "B'C"])
    assert "item_id IN ('A'" in sql
    assert "'B''C'" in sql


def test_build_no_filter_when_none():
    sql = _sql()
    assert "item_id IN" not in sql


def test_probe_sql_has_limit():
    sql = build_item_probe_sql(_conds(), date(2026, 1, 1), date(2026, 1, 1),
                               _cat(), limit=123)
    assert "SELECT DISTINCT item_id" in sql
    assert "LIMIT  123" in sql
