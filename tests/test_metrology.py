"""기능 B — inline 계측(fab.f_fab_wf_met) 매칭과 top-k 유의 인자.

확정 규칙:
  - 조회는 **분석 대상 lot으로 한정**한다(전체 스캔 금지)
  - subitem: site level은 `RANGE/STD/MIN/VALUE/SLOTID/Q2/MAX` 제외,
    wafer level은 `Q2`
  - 통계 분석 item 필터는 `CD|THK|DEPTH|TIP|RCS`
  - wafer 집계는 `model/aggregate`를 재사용한다(화면·표·PPT와 같은 숫자)
"""
from __future__ import annotations

import os

import polars as pl
import pytest

from etreport.data import metrology as mt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROW = {"root_lot_id": "PA100", "wafer_id": "01", "step_id": "M1",
       "item_id": "CD_A", "subitem_id": "SITE1", "fab_value": 10.0,
       "line_id": "KFBK", "tkout_time": None}


def _met(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame([{**ROW, **r} for r in rows])


@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


# ── SQL ──────────────────────────────────────────────────────
def test_sql_requires_lots():
    """★ lot 없이 부르면 전체 스캔이 되므로 막는다."""
    with pytest.raises(ValueError):
        mt.build_met_sql([])


def test_sql_narrows_to_lots_steps_items():
    sql = mt.build_met_sql(["PA100", "PB200"], steps=["M1"], items=["CD_A"],
                           item_regex=mt.ITEM_REGEX)

    assert "fab.f_fab_wf_met" in sql
    assert "root_lot_id IN ('PA100', 'PB200')" in sql
    assert "step_id IN ('M1')" in sql
    assert "item_id IN ('CD_A')" in sql
    assert "item_id REGEXP 'CD|THK|DEPTH|TIP|RCS'" in sql
    for col in mt.COLUMNS:                     # 컬럼명 원본 보존
        assert col in sql


# ── subitem 규칙 ─────────────────────────────────────────────
@pytest.mark.parametrize("sub", list(mt.SITE_EXCLUDE))
def test_site_level_drops_summary_subitems(sub):
    df = _met([{"subitem_id": sub}, {"subitem_id": "SITE1"}])
    got = mt.site_rows(df)["subitem_id"].to_list()
    assert got == ["SITE1"]


def test_wafer_level_uses_q2():
    df = _met([{"subitem_id": "Q2", "fab_value": 9.0},
               {"subitem_id": "SITE1", "fab_value": 5.0},
               {"subitem_id": "MAX", "fab_value": 99.0}])

    got = mt.wafer_rows(df)
    assert got.height == 1 and got["fab_value"][0] == 9.0


def test_subitem_matching_is_case_insensitive():
    df = _met([{"subitem_id": "q2"}, {"subitem_id": "std"},
               {"subitem_id": "site3"}])

    assert mt.wafer_rows(df).height == 1              # 'q2'도 Q2로 본다
    assert mt.site_rows(df)["subitem_id"].to_list() == ["site3"]  # q2·std 제외


def test_site_level_averages_points_per_wafer():
    df = _met([{"subitem_id": "SITE1", "fab_value": 10.0},
               {"subitem_id": "SITE2", "fab_value": 20.0}])
    vals = mt.wafer_values(df, "site")
    assert vals["value"].to_list() == [15.0]


def test_metric_name_keeps_step_and_item():
    """서로 다른 step의 같은 item이 한 열로 뭉치면 안 된다."""
    df = _met([{"step_id": "M1", "item_id": "CD_A", "subitem_id": "Q2"},
               {"step_id": "M5", "item_id": "CD_A", "subitem_id": "Q2"}])
    wide = mt.to_wide(df)
    assert sorted(c for c in wide.columns if "::" in c) == ["M1::CD_A",
                                                            "M5::CD_A"]


# ── ET 프레임에 붙이기 ───────────────────────────────────────
def _et() -> pl.DataFrame:
    return pl.DataFrame({
        "key": ["1", "2", "3", "4"],
        "lot": ["PA100"] * 4,
        "wafer": ["01", "01", "02", "02"],
        "gid": ["g0", "g0", "g1", "g1"],
        "Vt": [0.40, 0.42, 0.50, 0.52]})


def test_attach_joins_on_lot_and_wafer():
    """★ 계측값이 분석 프레임의 x축으로 쓸 수 있게 붙는다."""
    met = _met([{"wafer_id": "01", "subitem_id": "Q2", "fab_value": 10.0},
                {"wafer_id": "02", "subitem_id": "Q2", "fab_value": 20.0}])

    out, names = mt.attach(_et(), met)

    assert names == ["M1::CD_A"]
    assert out["M1::CD_A"].to_list() == [10.0, 10.0, 20.0, 20.0]
    assert out.height == 4                     # 행이 늘지 않는다


def test_attach_skips_existing_columns():
    met = _met([{"wafer_id": "01", "item_id": "Vt", "step_id": "",
                 "subitem_id": "Q2"}])
    out, names = mt.attach(_et().rename({"Vt": "::Vt"}), met)
    assert names == [] and out.width == _et().width


def test_attach_without_match_leaves_nulls():
    met = _met([{"wafer_id": "09", "subitem_id": "Q2", "fab_value": 7.0}])
    out, names = mt.attach(_et(), met)
    assert names == ["M1::CD_A"]
    assert out["M1::CD_A"].null_count() == 4


# ── top-k ────────────────────────────────────────────────────
def test_top_factors_ranks_correlation():
    """★ 상관이 큰 인자가 위로 온다 — wafer 집계는 aggregate를 재사용."""
    data = pl.DataFrame({
        "key": [str(i) for i in range(6)],
        "lot": ["PA"] * 6,
        "wafer": ["01", "02", "03", "04", "05", "06"],
        "gid": ["g0", "g0", "g0", "g1", "g1", "g1"],
        "Vt": [0.40, 0.44, 0.48, 0.52, 0.56, 0.60],
        "M1::CD": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],     # Vt와 완전 상관
        "M1::THK": [5.0, 4.0, 6.0, 5.0, 4.5, 5.5]})         # 무관

    top = mt.top_factors(data, ["M1::CD", "M1::THK"], ["Vt"], k=2)

    assert top["met"][0] == "M1::CD"
    assert abs(top["r"][0]) > 0.99
    assert top["n"][0] == 6
    assert top["score"][0] >= top["score"][1]


def test_top_factors_sees_group_difference():
    """상관이 없어도 그룹 간 차이가 크면 유의 인자로 올라온다."""
    data = pl.DataFrame({
        "key": [str(i) for i in range(6)],
        "lot": ["PA"] * 6,
        "wafer": ["01", "02", "03", "04", "05", "06"],
        "gid": ["g0", "g0", "g0", "g1", "g1", "g1"],
        "Vt": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
        "M1::CD": [10.0, 10.1, 9.9, 20.0, 20.1, 19.9]})     # 그룹이 완전히 갈린다

    top = mt.top_factors(data, ["M1::CD"], ["Vt"], k=5)

    assert top.height == 1
    assert abs(top["t"][0]) > 3


def test_top_factors_needs_three_points():
    data = pl.DataFrame({
        "key": ["1", "2"], "lot": ["PA", "PA"], "wafer": ["01", "02"],
        "gid": ["", ""], "Vt": [0.4, 0.5], "M1::CD": [1.0, 2.0]})
    assert mt.top_factors(data, ["M1::CD"], ["Vt"]).is_empty()


def test_top_factors_respects_exclusions():
    """제외한 점은 통계에서도 빠진다(화면과 같은 기준)."""
    data = pl.DataFrame({
        "key": ["1", "2", "3", "4"],
        "lot": ["PA"] * 4, "wafer": ["01", "02", "03", "04"],
        "gid": [""] * 4,
        "Vt": [0.4, 0.45, 0.5, 9.9],
        "M1::CD": [10.0, 11.0, 12.0, 13.0]})

    full = mt.top_factors(data, ["M1::CD"], ["Vt"])
    cut = mt.top_factors(data, ["M1::CD"], ["Vt"], excluded={"4"})

    assert full["n"][0] == 4 and cut["n"][0] == 3
    assert abs(cut["r"][0]) > abs(full["r"][0])          # 이상점이 빠져 상관↑


# ── 입력 파싱·필터 ───────────────────────────────────────────
def test_item_regex_filter():
    got = mt.match_regex(["CD_A", "THK_B", "VT_X", "rcs_1", "DEPTH_2", "ZZZ"])
    assert got == ["CD_A", "THK_B", "rcs_1", "DEPTH_2"]


def test_parse_pasted_pairs():
    text = "step_id\titem_id\nM1\tCD_A\nM5\tTHK_B\n\nM7,DEPTH_C"
    assert mt.parse_pairs(text) == [("M1", "CD_A"), ("M5", "THK_B"),
                                    ("M7", "DEPTH_C")]


# ── 대화창 흐름: [불러오기]는 조회만, [분석에 활용]으로 붙인다 ─────
def _dialog(qapp):
    """offscreen으로 계측 대화창을 조립 — state.data만 담고 나머지는 비운다.

    ① 계약: **버튼을 누르기 전에는 결과만 조회한 상태**다. 즉 `_load_done`이
    `state.data`에 붙이면 안 되고, `_use`를 눌렀을 때만 붙어야 한다.
    """
    from etreport.model.state import AppState
    from etreport.ui.widgets.metrology_dialog import MetrologyDialog

    state = AppState()
    state.data = _et()
    dlg = MetrologyDialog(state, None)
    return dlg, state


def test_load_done_does_not_attach_yet(qapp):
    """조회 완료(로드)만으로는 `met_columns`에 안 붙는다 — 조회 상태를 유지."""
    dlg, state = _dialog(qapp)
    met = _met([{"wafer_id": "01", "subitem_id": "Q2", "fab_value": 10.0},
                {"wafer_id": "02", "subitem_id": "Q2", "fab_value": 20.0}])

    dlg._load_done(met, n_lots=1)

    assert dlg.btn_use.isEnabled()
    assert state.met_columns == []
    assert "M1::CD_A" not in state.data.columns


def test_use_attaches_to_data(qapp):
    """[분석에 활용]을 누르면 그제서야 분석 프레임에 붙는다."""
    dlg, state = _dialog(qapp)
    dlg._load_done(_met([{"wafer_id": "01", "subitem_id": "Q2", "fab_value": 10.0},
                         {"wafer_id": "02", "subitem_id": "Q2", "fab_value": 20.0}]),
                   n_lots=1)

    dlg._use()

    assert state.met_columns == ["M1::CD_A"]
    assert state.data["M1::CD_A"].to_list() == [10.0, 10.0, 20.0, 20.0]


def test_use_without_load_is_guarded(qapp):
    """조회 전 [분석에 활용]을 누르면 안내만 하고 건드리지 않는다."""
    dlg, state = _dialog(qapp)

    dlg._use()

    assert state.met_columns == []
    assert "조회" in dlg.lbl.text()


def test_use_skips_when_names_exist(qapp):
    """이미 붙은 계측 열이면 새로 붙이지 않고 안내만 한다."""
    dlg, state = _dialog(qapp)
    met = _met([{"wafer_id": "01", "item_id": "Vt", "step_id": "",
                 "subitem_id": "Q2"}])
    dlg._load_done(met, n_lots=1)

    dlg._use()
    before = state.data.width
    dlg._use()

    assert state.data.width == before
    assert dlg.btn_use.isEnabled()
