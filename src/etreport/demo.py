"""데모 데이터 — 사내 DB·bdq 없이도 모든 화면이 그려지도록 채워 넣는다.

실제 연결 시에는 load_demo() 대신
  state.rf     = reformatter.load(경로)
  state.report = templates.build_report(...)
  state.data   = loader.load_state(state, db_path)  (읽기 전용)
로 갈아끼우면 나머지 UI는 그대로 동작한다.
"""
from __future__ import annotations

import hashlib
import random

import polars as pl

from etreport.data.reformatter import Reformatter, Rule
from etreport.model.specs import GroupStyle, PageSpec, PlotSpec, ReportSpec, TableRowSpec
from etreport.model.split import SplitMatrix
from etreport.model.state import AppState

# ── 리포메터 (실제 컬럼 스키마) ───────────────────────────────
_RULES = [
    # category itemid            alias           abs  scale form unit  low   high  target  w      l
    ("REAL", "ET_IDSAT_N_SVT", "Idsat N SVT", False, 1e6, "", "uA", 0.34, 0.86, 0.60, 0.20, 0.03),
    ("REAL", "ET_IDSAT_N_LVT", "Idsat N LVT", False, 1e6, "", "uA", None, None, None, 0.18, 0.03),
    ("REAL", "ET_VTLIN_N_SVT", "Vtlin N SVT", False, 1.0, "", "V",  0.30, 0.62, 0.46, 0.22, 0.03),
    ("REAL", "ET_VTSAT_N_SVT", "Vtsat N SVT", False, 1.0, "", "V",  0.36, 0.70, 0.53, 0.20, 0.035),
    ("REAL", "ET_IOFF_N_SVT",  "Ioff N SVT",  True,  1e9, "", "nA", None, 0.90, None, 0.15, 0.03),
    ("REAL", "ET_IDSAT_P_SVT", "Idsat P SVT", True,  1e6, "", "uA", 0.32, None, 0.50, 0.25, 0.04),
    ("REAL", "ET_VTLIN_P_SVT", "Vtlin P SVT", True,  1.0, "", "V",  0.28, 0.66, 0.47, 0.25, 0.04),
    ("REAL", "ET_CAP_MIM",     "Cap MIM unit", False, 1e15,"", "fF", None, None, None, None, None),
    ("ADDP", "CALC_RATIO_NP",  "Idsat N/P",   False, 1.0,
     "{Idsat N SVT}/{Idsat P SVT}", "", 1.8, 2.6, 2.2, None, None),
    ("ADDP", "CALC_VT_SPREAD", "Vt spread N", False, 1.0,
     "Std({Vtlin N SVT},{Vtsat N SVT})", "V", None, 0.06, None, None, None),
]

_SPLIT = [
    ("PA123", "W01", "Split_A", "Base"), ("PA123", "W02", "Split_A", "Hi"),
    ("PA123", "W03", "Split_B", "Base"), ("PA123", "W04", "Split_B", "Base"),
    ("PA124", "W01", "Split_B", "Hi"),   ("PA124", "W02", "Base", "Base"),
    ("PA124", "W03", "Base", "Hi"),
]


def _reformatter() -> Reformatter:
    rf = Reformatter()
    for i, (cat, iid, alias, ab, sc, form, unit, lo, hi, tg, w, length) in enumerate(_RULES, 2):
        rf.rules.append(Rule(cat, iid, alias, ab, sc, form, unit, lo, hi, tg, i,
                             w=w, l=length))
    return rf


def _report() -> ReportSpec:
    r = ReportSpec(report="M2_ET")
    p1 = PageSpec(1, "NMOS 특성 비교")
    p1.slots[0] = PlotSpec("Idsat vs Vtlin", "Vtlin N SVT", "Idsat N SVT")
    p1.slots[1] = PlotSpec("SVT+LVT 중첩", "Vtlin N SVT, Vtsat N SVT",
                           "Idsat N SVT, Idsat N LVT", x_name="Vt [V]")
    p1.slots[2] = PlotSpec("요약 — NMOS", type="table")
    p1.slots[3] = PlotSpec("Ioff (자동 log)", "Vtlin N SVT", "Ioff N SVT")
    p2 = PageSpec(2, "PMOS · 상관")
    p2.slots[0] = PlotSpec("Idsat vs Vtlin (P)", "Vtlin P SVT", "Idsat P SVT")
    p2.slots[1] = PlotSpec("N/P ratio", "Vtlin N SVT", "Idsat N/P")
    p2.slots[2] = PlotSpec("요약 — PMOS", type="table")
    p3 = PageSpec(3, "기하(W/L) trend")
    p3.slots[0] = PlotSpec("Idsat vs W", type="trend", x="W",
                           y="Idsat N SVT, Vtlin N SVT, Ioff N SVT, Idsat P SVT")
    p3.slots[1] = PlotSpec("Vt vs L", type="trend", x="L",
                           y="Vtlin N SVT, Vtsat N SVT, Vtlin P SVT")
    p3.slots[2] = PlotSpec("요약 — 기하", type="table")
    r.pages = [p1, p2, p3]
    r.table_rows = [
        TableRowSpec("Idsat N SVT", ["NMOS", "Idsat", "SVT"]),
        TableRowSpec("Idsat N LVT", ["NMOS", "Idsat", "LVT"]),
        TableRowSpec("Vtlin N SVT", ["NMOS", "Vt", "lin"]),
        TableRowSpec("Vtsat N SVT", ["NMOS", "Vt", "sat"]),
        TableRowSpec("Idsat P SVT", ["PMOS", "Idsat", "SVT"]),
        TableRowSpec("Vtlin P SVT", ["PMOS", "Vt", "lin"]),
        TableRowSpec("Ioff N SVT", ["Leakage", "Ioff", "N"]),
        TableRowSpec("Idsat N/P", ["Ratio", "N/P", "Idsat"]),
        TableRowSpec("Vt spread N", ["Ratio", "Spread", "Vt"]),
    ]
    return r


def _points(rf: Reformatter, per_wafer: int = 24) -> pl.DataFrame:
    """wafer별 die 포인트. 조건 코드에 따라 평균을 살짝 밀어 실험 효과를 만든다."""
    rng = random.Random(20260811)
    rows: list[dict] = []
    for lot, waf, m1, m5 in _SPLIT:
        bias = {"Split_A": 0.05, "Split_B": -0.04, "Base": 0.0}[m1]
        bias += {"Hi": 0.02, "Base": 0.0}[m5]
        for i in range(per_wafer):
            vt = 0.46 + bias * 0.6 + rng.gauss(0, 0.022)
            vts = vt + 0.07 + rng.gauss(0, 0.010)
            ids = 0.60 + bias + rng.gauss(0, 0.045) - (vt - 0.46) * 1.4
            idl = ids * 1.24 + rng.gauss(0, 0.03)
            ioff = max(1e-3, 0.16 * (10 ** (-(vt - 0.46) * 6)) + rng.gauss(0, 0.02))
            vtp = 0.47 - bias * 0.4 + rng.gauss(0, 0.020)
            idp = 0.50 - bias * 0.5 + rng.gauss(0, 0.035)
            cap = 1.84 + rng.gauss(0, 0.02)
            key = hashlib.blake2b(
                f"{lot}|{waf}|{i}".encode(), digest_size=8).hexdigest()
            rec = {
                "key": key, "lot": lot, "wafer": waf, "gid": "",
                "Idsat N SVT": ids, "Idsat N LVT": idl, "Vtlin N SVT": vt,
                "Vtsat N SVT": vts, "Ioff N SVT": ioff, "Idsat P SVT": idp,
                "Vtlin P SVT": vtp, "Cap MIM unit": cap,
            }
            rec["Idsat N/P"] = ids / idp if idp else None
            mu = (vt + vts) / 2
            rec["Vt spread N"] = ((vt - mu) ** 2 + (vts - mu) ** 2) ** 0.5
            rows.append(rec)
    return pl.DataFrame(rows)


def load_demo(state: AppState) -> None:
    state.rf = _reformatter()
    state.report = _report()
    state.reports = ["M2_ET", "DEV_EVAL"]
    state.split = SplitMatrix.from_dataframe(pl.DataFrame(
        {"lot": [r[0] for r in _SPLIT], "wafer": [r[1] for r in _SPLIT],
         "M1": [r[2] for r in _SPLIT], "M5": [r[3] for r in _SPLIT]}))
    state.factors = ["M1"]
    state.data = _points(state.rf)
    apply_split(state)
    state.explore = PlotSpec(x="Vtlin N SVT", y="Idsat N SVT")


def apply_split(state: AppState) -> None:
    """현재 factor로 그룹을 다시 만들고 포인트에 gid를 배정한다."""
    if state.split is None or state.data is None:
        return
    from etreport.model import wafers
    state.groups = state.split.styles_for(state.factors)
    assign = state.split.assignment(state.factors)
    gids = wafers.map_gids(state.data["lot"], state.data["wafer"], assign)
    state.data = state.data.with_columns(pl.Series("gid", gids))


def unassigned_groups(state: AppState) -> list[GroupStyle]:
    return [g for g in state.groups if g.visible]
