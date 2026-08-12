"""item 확인 다이얼로그 — 순수 대조 함수."""
from __future__ import annotations

from etreport.ui.widgets.item_check_dialog import diff_items


def test_diff_items_basic():
    rf_only, ac_only = diff_items(["A", "B"], ["A", "C"])
    assert rf_only == ["B"]          # 리포메터에만
    assert ac_only == ["C"]          # 실제에만


def test_diff_items_empty():
    assert diff_items([], []) == ([], [])
    assert diff_items(["A"], None) == (["A"], [])
