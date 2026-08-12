"""§3.1 리포메터 셀 해석 — ABSOLUTE 참/거짓 판정과 ADDP 함수 목록.

확정 사양:
  ABSOLUTE  `TRUE/True/true/T/Y/1/O` = 참, `FALSE/N/0/X/빈칸` = 거짓 (대소문자 무관)
  함수      `ABS SQRT LN LOG LOG10 EXP MIN MAX AVG SUM STD`
            **LN = 자연로그(밑 e), LOG·LOG10 = 상용로그(밑 10)** — 엑셀 관례

둘 다 "조용히 틀리는" 종류다. ABSOLUTE를 `TRUE`로 적으면 절대값이 그냥 안 걸리고,
`LOG(...)`를 쓰면 그 ADDP만 통째로 빠진 채 나머지가 정상으로 보인다.
"""
from __future__ import annotations

import math

import polars as pl
import pytest

from etreport.data.reformatter import apply as rf_apply
from etreport.data.reformatter import compile_formula, load, parse_flag

COLS = ["CATEGORY", "ITEMID", "ALIAS", "ABSOLUTE", "SCALE FACTOR",
        "ADDP FORM", "UNIT", "SPECLOW", "SPECHIGH", "TARGET"]


def _sheet(rows: list[dict]) -> pl.DataFrame:
    """리포메터 시트 모양 — xlio.read_sheet가 돌려주는 것과 같은 형태."""
    return pl.DataFrame([{c: r.get(c) for c in COLS} for r in rows],
                        strict=False)


def _real(alias: str, itemid: str, absolute=None, scale=1.0) -> dict:
    return {"CATEGORY": "REAL", "ITEMID": itemid, "ALIAS": alias,
            "ABSOLUTE": absolute, "SCALE FACTOR": scale}


def _addp(alias: str, formula: str, absolute=None) -> dict:
    return {"CATEGORY": "ADDP", "ITEMID": None, "ALIAS": alias,
            "ABSOLUTE": absolute, "SCALE FACTOR": 1.0, "ADDP FORM": formula}


def _long(itemid: str, values: list[float]) -> pl.DataFrame:
    return pl.DataFrame({
        "root_lot_id": ["PA1"] * len(values),
        "wafer_id": ["01"] * len(values),
        "chip_x_pos": list(range(len(values))),
        "item_id": [itemid] * len(values),
        "value": values,
    })


# ── ABSOLUTE 판정 ────────────────────────────────────────────
@pytest.mark.parametrize("cell", ["TRUE", "True", "true", "T", "Y", "y",
                                  "1", "O", "o", " Y ", 1.0, 1, True, "1.0"])
def test_truthy_cells(cell):
    assert parse_flag(cell) is True


@pytest.mark.parametrize("cell", ["FALSE", "False", "N", "n", "0", "X", "x",
                                  "", "  ", None, 0.0, 0, False, "0.0"])
def test_falsy_cells(cell):
    assert parse_flag(cell) is False


@pytest.mark.parametrize("cell", ["예", "아니오", "maybe", 2.0, "1.5"])
def test_unknown_cells_are_reported_not_guessed(cell):
    """모르는 값은 None — 호출부가 경고를 남기고 거짓으로 처리한다."""
    assert parse_flag(cell) is None


def test_load_reads_every_truthy_spelling(fake_sheet):
    """★ 'Y'만 참으로 보던 시절에는 TRUE·1·O로 적은 행의 절대값이 무시됐다."""
    fake_sheet(_sheet([
        _real("a", "P1", absolute="TRUE"),
        _real("b", "P2", absolute="1"),
        _real("c", "P3", absolute="O"),
        _real("d", "P4", absolute="Y"),
        _real("e", "P5", absolute="X"),
        _real("f", "P6", absolute=None),
    ]))

    rf = load("아무_경로.xlsx")

    assert [r.absolute for r in rf.rules] == [True, True, True, True,
                                              False, False]
    assert not rf.warnings


def test_unknown_absolute_warns_but_keeps_the_row(fake_sheet):
    """버리지 않고 거짓으로 진행하되 이유를 남긴다 — 이 코드베이스의 오류 처리."""
    fake_sheet(_sheet([_real("a", "P1", absolute="예")]))

    rf = load("아무_경로.xlsx")

    assert [r.absolute for r in rf.rules] == [False]
    assert len(rf.warnings) == 1
    assert rf.warnings[0].alias == "a" and "ABSOLUTE" in rf.warnings[0].message


def test_absolute_true_actually_flips_the_sign(fake_sheet):
    """리포메팅까지 태워 확인 — 판정이 값에 실제로 반영되는지."""
    fake_sheet(_sheet([_real("Ioff", "P1", absolute="TRUE", scale=2.0)]))
    rf = load("아무_경로.xlsx")

    out = rf_apply(rf, _long("P1", [-1.5, 2.0]))

    assert sorted(out["value"]) == pytest.approx([3.0, 4.0])   # |v × 2|


# ── ADDP 함수 목록 ───────────────────────────────────────────
@pytest.mark.parametrize("src,arg,expect", [
    ("Log({A})", 100.0, 2.0),                 # 상용로그 (엑셀 관례)
    ("LOG({A})", 1000.0, 3.0),
    ("Log10({A})", 100.0, 2.0),
    ("Ln({A})", math.e, 1.0),                 # 자연로그
    ("Exp({A})", 1.0, math.e),
    ("EXP({A})", 0.0, 1.0),
])
def test_row_engine_semantics(src, arg, expect):
    assert compile_formula(src)({"A": arg}) == pytest.approx(expect)


@pytest.mark.parametrize("src,arg", [("Log({A})", 0.0), ("Log({A})", -1.0),
                                     ("Exp({A})", 1e9)])
def test_out_of_domain_is_null(src, arg):
    """정의역 밖(0·음수)과 오버플로는 inf가 아니라 NULL."""
    assert compile_formula(src)({"A": arg}) is None


def test_log_and_exp_survive_validation_and_compute(fake_sheet):
    """★ 예전에는 'LOG'·'EXP'가 화이트리스트에 없어 그 ADDP가 통째로 빠졌다."""
    fake_sheet(_sheet([
        _real("Ioff", "P1"),
        _addp("logIoff", "Log({Ioff})"),
        _addp("expIoff", "Exp({Ioff})"),
    ]))

    rf = load("아무_경로.xlsx")
    assert not rf.warnings and not rf.errors
    assert [r.alias for r in rf.addps()] == ["logIoff", "expIoff"]

    out = rf_apply(rf, _long("P1", [100.0]))
    got = {r["item_id"]: r["value"] for r in out.iter_rows(named=True)}
    assert got["logIoff"] == pytest.approx(2.0)
    assert got["expIoff"] == pytest.approx(math.exp(100.0))
