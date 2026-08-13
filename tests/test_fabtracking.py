"""기능 A — fab tracking 기반 자동 split grouping.

확정 규칙:
  - `line_id='KFBK'` + `ein_ecn_no IS NOT NULL`로 split 실험 lot을 먼저 본다
  - **area='PHOTO'는 recipe(reticle_id), 그 외는 ppid**로 조건을 비교한다
  - 유효 wafer 안에서 **조건이 갈리는 step만** 실험 축으로 올린다
  - 그룹핑은 `model/split.SplitMatrix`를 그대로 쓴다(중복 구현 금지)
  - 추출 컬럼명은 원본 그대로 (`part_id`·`ppid`·`ein_ecn_no` …)
"""
from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from etreport.data import fabtracking as ft

ROW = {"part_id": "DEV1", "process_id": "M1", "step_seq": 1,
       "root_lot_id": "PA100", "wafer_id": "01", "area": "ETCH",
       "tkout_time": datetime(2026, 8, 4, 9, 0), "foup_id": "F1",
       "eqp_model": "EM", "eqp_id": "EQ1", "unit_id": "U1",
       "chamber_id": "C1", "ppid": "STD", "reticle_id": "RTC1",
       "ein_ecn_no": "ECN-1", "line_id": "KFBK"}


def _df(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame([{**ROW, **r} for r in rows])


# ── SQL ──────────────────────────────────────────────────────
def test_sql_keeps_original_column_names():
    """★ 추출 경로에서 컬럼명을 축약·변형하지 않는다."""
    sql = ft.build_tracking_sql()

    for col in ("part_id", "process_id", "step_seq", "root_lot_id", "wafer_id",
                "area", "tkout_time", "foup_id", "eqp_model", "eqp_id",
                "unit_id", "chamber_id", "ppid", "reticle_id", "ein_ecn_no",
                "line_id"):
        assert col in sql
    assert "fab.f_fab_tracking" in sql


def test_sql_defaults_to_kfbk_and_ecn_rows():
    sql = ft.build_tracking_sql()
    assert "line_id = 'KFBK'" in sql
    assert "ein_ecn_no IS NOT NULL" in sql


def test_sql_narrows_by_lot_and_period():
    sql = ft.build_tracking_sql(lots=["PA100", "PB200"],
                                d_from=date(2026, 8, 4), d_to=date(2026, 8, 10))
    assert "root_lot_id IN ('PA100', 'PB200')" in sql
    # 기간은 문자열 리터럴 · 끝날은 +1일 미만 (ET 추출과 같은 규칙)
    assert "tkout_time >= '2026-08-04 00:00:00'" in sql
    assert "tkout_time <  '2026-08-11 00:00:00'" in sql


def test_sql_can_include_non_ecn_rows():
    """컬럼은 늘 SELECT하되 필터만 뺀다 — ecn이 없는 lot도 볼 수 있어야 한다."""
    sql = ft.build_tracking_sql(ecn_only=False)
    where = sql.split("WHERE")[1]
    assert "ein_ecn_no" not in where
    assert "ein_ecn_no" in sql.split("FROM")[0]


# ── PHOTO는 recipe, 그 외는 ppid ─────────────────────────────
def test_photo_uses_recipe_others_use_ppid():
    """★ area에 따라 비교 컬럼이 달라진다."""
    df = _df([
        {"wafer_id": "01", "process_id": "M1", "area": "PHOTO",
         "reticle_id": "RA", "ppid": "SAME"},
        {"wafer_id": "02", "process_id": "M1", "area": "PHOTO",
         "reticle_id": "RB", "ppid": "SAME"},        # recipe만 다르다
        {"wafer_id": "01", "process_id": "M5", "area": "ETCH",
         "reticle_id": "RX", "ppid": "P1"},
        {"wafer_id": "02", "process_id": "M5", "area": "ETCH",
         "reticle_id": "RY", "ppid": "P1"},          # ppid는 같다 → split 아님
    ])

    cond = ft.wafer_conditions(df)
    photo = cond.filter(pl.col("step_id") == "M1")["condition"].to_list()
    etch = cond.filter(pl.col("step_id") == "M5")["condition"].to_list()

    assert sorted(photo) == ["RA", "RB"]             # PHOTO → reticle_id
    assert etch == ["P1", "P1"]                      # 그 외 → ppid
    assert ft.split_steps(df) == ["M1"]              # 갈리는 step만


def test_photo_area_is_case_insensitive():
    df = _df([{"wafer_id": "01", "process_id": "M1", "area": "photo",
               "reticle_id": "RA", "ppid": "P1"},
              {"wafer_id": "02", "process_id": "M1", "area": "Photo",
               "reticle_id": "RB", "ppid": "P1"}])
    assert ft.split_steps(df) == ["M1"]


def test_rework_takes_the_last_condition():
    """같은 step을 두 번 지났으면 마지막 조건이 실험 조건이다."""
    df = _df([
        {"wafer_id": "01", "process_id": "M1", "ppid": "OLD",
         "tkout_time": datetime(2026, 8, 4, 9, 0)},
        {"wafer_id": "01", "process_id": "M1", "ppid": "NEW",
         "tkout_time": datetime(2026, 8, 4, 18, 0)},
    ])
    cond = ft.wafer_conditions(df)
    assert cond["condition"].to_list() == ["NEW"]


def test_uniform_step_is_not_a_split():
    df = _df([{"wafer_id": "01", "process_id": "M1", "ppid": "P1"},
              {"wafer_id": "02", "process_id": "M1", "ppid": "P1"}])
    assert ft.split_steps(df) == []


# ── SplitMatrix 연결 (그룹핑 로직 재사용) ────────────────────
def test_split_matrix_uses_existing_grouping():
    """★ 별도 grouping 로직을 만들지 않고 SplitMatrix로 넘긴다."""
    df = _df([
        {"wafer_id": "01", "process_id": "M1", "ppid": "Base"},
        {"wafer_id": "02", "process_id": "M1", "ppid": "Base"},
        {"wafer_id": "03", "process_id": "M1", "ppid": "Hi"},
        {"wafer_id": "01", "process_id": "M5", "ppid": "Base"},
        {"wafer_id": "02", "process_id": "M5", "ppid": "Base"},
        {"wafer_id": "03", "process_id": "M5", "ppid": "Base"},
    ])

    sm = ft.to_split_matrix(df)

    assert sm.steps == ["M1"]                        # 갈리는 step만 factor 후보
    assert sm.baseline == "Base"                     # 다수 조건이 기준(REF)
    styles = sm.styles_for(["M1"])
    assert [s.ref for s in styles] == [True, False]
    assert sm.assignment(["M1"])[("PA100", "03")] != \
        sm.assignment(["M1"])[("PA100", "01")]


def test_confound_detection_still_works():
    """혼입 감지(기능의 핵심)가 fab tracking 매트릭스에서도 동작한다."""
    df = _df([
        {"wafer_id": "01", "process_id": "M1", "ppid": "Base"},
        {"wafer_id": "02", "process_id": "M1", "ppid": "Hi"},
        {"wafer_id": "03", "process_id": "M1", "ppid": "Hi"},
        {"wafer_id": "01", "process_id": "M5", "ppid": "Base"},
        {"wafer_id": "02", "process_id": "M5", "ppid": "Base"},
        {"wafer_id": "03", "process_id": "M5", "ppid": "Alt"},   # M5도 갈린다
    ])

    sm = ft.to_split_matrix(df)
    cf = sm.confounds(["M1"])

    assert sm.steps == ["M1", "M5"]
    assert cf and cf[0].step == "M5"                 # M1 그룹 안에 M5가 섞였다


def test_multi_lot_grouping_survives():
    """★ 멀티 lot에서도 grouping이 깨지지 않는다."""
    df = _df([
        {"root_lot_id": "PA100", "wafer_id": "01", "process_id": "M1",
         "ppid": "Base"},
        {"root_lot_id": "PA100", "wafer_id": "02", "process_id": "M1",
         "ppid": "Hi"},
        {"root_lot_id": "PB200", "wafer_id": "01", "process_id": "M1",
         "ppid": "Base"},
        {"root_lot_id": "PB200", "wafer_id": "02", "process_id": "M1",
         "ppid": "Lo"},
    ])

    sm = ft.to_split_matrix(df)
    assign = sm.assignment(["M1"])

    assert sm.wide.height == 4
    assert assign[("PA100", "01")] == assign[("PB200", "01")]   # 둘 다 Base
    assert len({*assign.values()}) == 3                          # Base·Hi·Lo
    assert ft.split_steps(df) == ["M1"]


def test_empty_input_is_handled():
    empty = pl.DataFrame(schema=dict.fromkeys(ft.COLUMNS, pl.Utf8))
    assert ft.split_steps(empty) == []
    sm = ft.to_split_matrix(empty)
    assert sm.steps == []


def test_summary_line_mentions_split_steps():
    df = _df([{"wafer_id": "01", "process_id": "M1", "ppid": "Base"},
              {"wafer_id": "02", "process_id": "M1", "ppid": "Hi"}])
    assert "M1" in ft.summarize(df)


# ── 추출 정규화 공유 (§10.4) ─────────────────────────────────
def test_categorical_and_time_are_normalised():
    from etreport.data.extractor import normalize_categoricals

    df = pl.DataFrame({
        "ppid": pl.Series(["P1", "P1"], dtype=pl.Categorical),
        "tkout_time": ["2026-08-04 09:00:00", "2026-08-04 10:00:00"],
        "step_seq": [1, 2]})

    out = normalize_categoricals(df)

    assert out.schema["ppid"] == pl.Utf8
    assert out.schema["tkout_time"] == pl.Datetime("us")
    assert out["tkout_time"].null_count() == 0
    assert out.columns == ["ppid", "tkout_time", "step_seq"]   # 이름 보존


@pytest.mark.parametrize("bad", [None, ""])
def test_missing_condition_falls_back_to_baseline(bad):
    df = _df([{"wafer_id": "01", "process_id": "M1", "ppid": "Base"},
              {"wafer_id": "02", "process_id": "M1", "ppid": "Hi"},
              {"wafer_id": "03", "process_id": "M1", "ppid": bad}])
    sm = ft.to_split_matrix(df)
    assert sm.wide.height == 3
    assert None not in sm.wide["M1"].to_list()
