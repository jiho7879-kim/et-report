"""데모 데이터의 **정의** — 리포메터·템플릿·실험 조건·raw 측정값 한 벌.

여기 있는 표들이 데모의 단일 진실이다. 화면에 바로 올리는 in-memory 상태
(`demo.load_demo`)도, 파일로 떨어뜨리는 번들(`demo_bundle`)도 전부 이 모듈이
만든 같은 표에서 나온다 — 그래야 "데모 화면과 데모 파일이 다르다"가 없다.

데이터는 **기능을 하나씩 밟도록** 일부러 설계했다.

  · lot 4개 · wafer 43장 (한 lot은 25장 — 표 분할/넘침 §7.3을 눈으로 본다)
  · step 2종 · 온도 2종(raw 23.9·84.6·148.5 → 25·85·150 보정 §10) · site 수 2종
  · **NMOS는 step_seq 1, PMOS·누설은 step_seq 2** — 읽으면서 한 점으로 합치는
    §10.1 병합이 없으면 산점도가 통째로 빈다
  · retest 행(같은 키 · 더 이른 시각) — QUALIFY로 최신만 남는지
  · 음수로 기록되는 PMOS·누설 — ABSOLUTE 재적용(§10.2)
  · NULL(미측정)·0(분모)·아주 작은 값(로그축) 함정
  · REAL 13 · ADDP 12 — ABS SQRT LN LOG10 EXP MIN MAX AVG SUM STD를 전부 쓰고
    ADDP가 ADDP를 참조하는 사슬도 들어 있다
  · 리포트 2종(M2_ET · DEV_EVAL) · CAT 4단 · trend(W·L) · Mode 4종

값은 seed 고정이라 언제 만들어도 같은 그림이 나온다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import polars as pl

from etreport.data.reformatter import Reformatter, from_frame

SEED = 20260815
LINE_ID = "KFBK"
D_FROM = date(2026, 8, 3)          # 데모 추출 기간 시작
D_TO = date(2026, 8, 5)            # (포함) — 하루 = lot 하나 남짓

REPORTS = ("M2_ET", "DEV_EVAL")

#: 3×3 / 5점 die 배치 — total_site_cnt와 짝을 맞춘다
DIES_9 = [(x, y) for y in (-2, 0, 2) for x in (-2, 0, 2)]
DIES_5 = [(0, 0), (-2, 0), (2, 0), (0, -2), (0, 2)]


# ── 계측 조건(lot 계획) ───────────────────────────────────────
@dataclass(frozen=True)
class LotPlan:
    lot: str
    wafers: tuple[str, ...]
    steps: tuple[str, ...]
    temps: tuple[float, ...]        # 계측기 raw 온도 (5단위 보정 전)
    dies: tuple[tuple[int, int], ...]
    day: date

    @property
    def site_cnt(self) -> int:
        return len(self.dies)


def _w(n: int) -> tuple[str, ...]:
    return tuple(f"W{i:02d}" for i in range(1, n + 1))


LOTS: tuple[LotPlan, ...] = (
    # 25장짜리 lot — 표가 한 슬라이드를 넘친다(overflow/split 확인용)
    LotPlan("PA123", _w(25), ("M2ET", "M3ET"), (23.9, 84.6),
            tuple(DIES_9), date(2026, 8, 3)),
    LotPlan("PA124", _w(8), ("M2ET", "M3ET"), (23.9, 84.6),
            tuple(DIES_9), date(2026, 8, 3)),
    # site 수가 다른 lot — 그룹 편집 4단 필터에서 site로 좁혀 본다
    LotPlan("PB331", _w(6), ("M2ET",), (23.9,), tuple(DIES_5), date(2026, 8, 4)),
    # 고온 150°C — 온도 보정과 온도별 그룹 배정
    LotPlan("PC777", _w(4), ("M2ET",), (23.9, 148.5), tuple(DIES_9),
            date(2026, 8, 5)),
)


# ── 실험 조건(split) ─────────────────────────────────────────
#: (lot, wafer) → {step: code}. 비워 둔 wafer는 **미배정** — 그룹이 없는
#: wafer가 화면·표에서 어떻게 보이는지도 데모에 필요하다.
_M1_CYCLE = ("Base", "Split_A", "Split_B")
_M5_CYCLE = ("Base", "Hi")


def split_frame() -> pl.DataFrame:
    """lot·wafer × 실험 step 3종. M8은 M1 안에서 갈려 **혼입 경고**를 만든다."""
    rows: list[dict] = []
    for plan in LOTS:
        for i, waf in enumerate(plan.wafers):
            if plan.lot == "PC777" and waf in ("W03", "W04"):
                continue                      # 미배정 wafer (의도적으로 남긴다)
            m1 = _M1_CYCLE[i % 3]
            m5 = _M5_CYCLE[(i // 3) % 2]
            # M8은 M1 그룹 안에서 두 값으로 갈린다 → confounds()가 잡아낸다
            m8 = "Slow" if (i % 6) < 3 else "Fast"
            rows.append({"lot": plan.lot, "wafer": waf,
                         "M1": m1, "M5": m5, "M8": m8})
    return pl.DataFrame(rows)


def _bias_map() -> dict[tuple[str, str], float]:
    """(lot, wafer) → 실험 효과. 조건이 그림에서 눈에 보이도록 밀어 준다."""
    m1 = {"Base": 0.0, "Split_A": 0.05, "Split_B": -0.04}
    m5 = {"Base": 0.0, "Hi": 0.02}
    out: dict[tuple[str, str], float] = {}
    for r in split_frame().iter_rows(named=True):
        out[(r["lot"], r["wafer"])] = m1[r["M1"]] + m5[r["M5"]]
    return out


# ── 리포메터 ─────────────────────────────────────────────────
#: (CATEGORY, ITEMID, ALIAS, ABSOLUTE, SCALE, ADDP FORM, UNIT,
#:  SPECLOW, SPECHIGH, TARGET, WIDTH, LENGTH)
RULE_ROWS: list[tuple] = [
    ("REAL", "ET_IDSAT_N_SVT", "Idsat N SVT", "N", 1e6, "", "uA",
     0.34, 0.86, 0.60, 0.20, 0.03),
    ("REAL", "ET_IDSAT_N_LVT", "Idsat N LVT", "N", 1e6, "", "uA",
     None, None, None, 0.18, 0.03),
    ("REAL", "ET_VTLIN_N_SVT", "Vtlin N SVT", "N", 1.0, "", "V",
     0.30, 0.62, 0.46, 0.22, 0.03),
    ("REAL", "ET_VTSAT_N_SVT", "Vtsat N SVT", "N", 1.0, "", "V",
     0.36, 0.70, 0.53, 0.20, 0.035),
    ("REAL", "ET_VTLIN_N_LVT", "Vtlin N LVT", "N", 1.0, "", "V",
     0.18, 0.42, 0.30, 0.18, 0.03),
    # 아래 셋은 원본이 음수로 기록된다 — ABSOLUTE 표기도 일부러 섞어 쓴다
    ("REAL", "ET_IOFF_N_SVT", "Ioff N SVT", "TRUE", 1e9, "", "nA",
     None, 0.90, None, 0.15, 0.03),
    ("REAL", "ET_IDSAT_P_SVT", "Idsat P SVT", "Y", 1e6, "", "uA",
     0.20, 0.34, 0.27, 0.25, 0.04),
    ("REAL", "ET_VTLIN_P_SVT", "Vtlin P SVT", "1", 1.0, "", "V",
     0.28, 0.66, 0.47, 0.25, 0.04),
    ("REAL", "ET_IOFF_P_SVT", "Ioff P SVT", "TRUE", 1e9, "", "nA",
     None, 1.20, None, 0.25, 0.04),
    ("REAL", "ET_JG_GATE", "Jg Gate", "TRUE", 1e9, "", "nA/um2",
     None, 5.00, None, 0.50, 0.50),
    # NULL(미측정)과 0(분모)이 섞여 들어오는 항목
    ("REAL", "ET_RS_POLY", "Rs Poly", "N", 1.0, "", "ohm/sq",
     180.0, 260.0, 220.0, 1.00, 0.50),
    # 특정 step에서만 측정된다 — 없는 조건에서는 통째로 NULL
    ("REAL", "ET_BVOX", "BVox", "N", 1.0, "", "V",
     5.50, None, 6.20, 0.50, 0.50),
    ("REAL", "ET_CAP_MIM", "Cap MIM unit", "N", 1e15, "", "fF",
     None, None, None, None, None),
    # ── ADDP — 시트 행 순서가 곧 계산 순서다 ────────────────
    ("ADDP", "", "Idsat N/P", "N", 1.0,
     "{Idsat N SVT}/{Idsat P SVT}", "", 1.8, 2.6, 2.2, None, None),
    ("ADDP", "", "Vt delta N", "N", 1.0,
     "{Vtsat N SVT}-{Vtlin N SVT}", "V", 0.05, 0.12, 0.08, None, None),
    ("ADDP", "", "Vt 평균", "N", 1.0,
     "Avg({Vtlin N SVT},{Vtsat N SVT},{Vtlin N LVT})", "V",
     None, None, None, None, None),
    ("ADDP", "", "Vt spread N", "N", 1.0,
     "Std({Vtlin N SVT},{Vtsat N SVT},{Vtlin N LVT})", "V",
     None, 0.13, None, None, None),
    ("ADDP", "", "Idsat 범위", "N", 1.0,
     "Max({Idsat N SVT},{Idsat N LVT})-Min({Idsat N SVT},{Idsat N LVT})", "uA",
     None, None, None, None, None),
    ("ADDP", "", "Leak 합", "N", 1.0,
     "Sum({Ioff N SVT},{Ioff P SVT},{Jg Gate})", "nA",
     None, 6.00, None, None, None),
    # LOG는 상용로그(밑 10) · LN은 자연로그 — 헷갈리기 쉬운 규칙을 데모에 남긴다.
    # 여기 수식은 같은 step_seq 안에서 참조한다. seq를 넘나드는 수식도
    # 리포메팅이 seq를 합쳐 계산한다(reformatter._fill_cross_seq).
    ("ADDP", "", "Ioff decade", "N", 1.0,
     "Log({Ioff N SVT})", "dec", None, 0.30, None, None, None),
    ("ADDP", "", "Rs 로그", "N", 1.0,
     "Ln({Rs Poly}+1)", "", None, None, None, None, None),
    # 분모에 0·NULL이 섞인다 — 0나눗셈은 조용히 NULL이어야 한다
    ("ADDP", "", "Idsat/Rs", "N", 1.0,
     "{Idsat N SVT}/{Rs Poly}", "uA·sq/ohm", None, None, None, None, None),
    ("ADDP", "", "Vt 편차 근사", "N", 1.0,
     "Sqrt(Abs({Vt delta N}))", "V^0.5", None, None, None, None, None),
    ("ADDP", "", "Exp 항", "N", 1.0,
     "Exp(-{Vt delta N}*10)", "", None, None, None, None, None),
    # ADDP가 ADDP를 참조 — 위 행만 참조할 수 있다는 규칙의 실제 예
    ("ADDP", "", "N/P 여유", "N", 1.0,
     "Abs({Idsat N/P}-2.2)", "", None, 0.40, None, None, None),
]

RF_COLUMNS = ["CATEGORY", "ITEMID", "ALIAS", "ABSOLUTE", "SCALE FACTOR",
              "ADDP FORM", "UNIT", "SPECLOW", "SPECHIGH", "TARGET",
              "WIDTH", "LENGTH"]


def reformatter_frame() -> pl.DataFrame:
    """리포메터 시트 모양의 표 (xlio.read_sheet가 돌려주는 것과 같은 형태)."""
    cols: dict[str, pl.Series] = {}
    for i, name in enumerate(RF_COLUMNS):
        vals = [r[i] for r in RULE_ROWS]
        if name in ("SPECLOW", "SPECHIGH", "TARGET", "WIDTH", "LENGTH",
                    "SCALE FACTOR"):
            cols[name] = pl.Series(name, vals, dtype=pl.Float64)
        else:
            cols[name] = pl.Series(name, [None if v in (None, "") else str(v)
                                          for v in vals], dtype=pl.Utf8)
    return pl.DataFrame(cols)


def reformatter() -> Reformatter:
    """검증까지 마친 데모 리포메터 (파일 없이)."""
    return from_frame(reformatter_frame())


def real_items() -> list[tuple[str, str]]:
    """(ITEMID, ALIAS) — REAL만, 시트 순서 그대로."""
    return [(r[1], r[2]) for r in RULE_ROWS if r[0] == "REAL"]


# ── 템플릿 ───────────────────────────────────────────────────
_PLOT_ROWS: list[tuple] = [
    # page, x, y, order, title1, title2, Report, Type, x_name, y_name, Mode
    (1, "Vtlin N SVT", "Idsat N SVT", 1, "NMOS 특성", "Idsat–Vtlin",
     "M2_ET", "scatter", "", "", "site"),
    (1, "Vtlin N SVT", "Ioff N SVT", 2, "", "Ioff–Vtlin (자동 로그축)",
     "M2_ET", "scatter", "", "", "site"),
    (1, "Vtlin N SVT, Vtsat N SVT", "Idsat N SVT, Idsat N LVT", 3, "",
     "SVT·LVT 겹쳐 보기", "M2_ET", "scatter", "Vt [V]", "", "site"),
    (1, "Vtlin N SVT", "Idsat N SVT", 4, "", "wafer 평균(avg)",
     "M2_ET", "scatter", "", "", "avg"),
    (1, "Vtlin N SVT", "Vt spread N", 5, "", "wafer 산포(std)",
     "M2_ET", "scatter", "", "", "std"),
    (1, "Rs Poly", "Idsat/Rs", 6, "", "0·NULL 분모 (빈 점이 없어야 한다)",
     "M2_ET", "scatter", "", "", "site"),
    (2, "Vtlin P SVT", "Idsat P SVT", 1, "PMOS · 비율", "Idsat–Vtlin (P)",
     "M2_ET", "scatter", "", "", "site"),
    (2, "Idsat N SVT", "Idsat N/P", 2, "", "N/P 비", "M2_ET",
     "scatter", "", "", "med"),
    (2, "Vt delta N", "Leak 합", 3, "", "누설 합계(로그축)",
     "M2_ET", "scatter", "", "", "site"),
    (2, "Vt 평균", "Exp 항", 4, "", "Exp 항", "M2_ET", "scatter", "", "", "site"),
    (2, "Ioff decade", "N/P 여유", 5, "", "ADDP 사슬 결과",
     "M2_ET", "scatter", "", "", "site"),
    (3, "W", "Idsat N SVT, Vtlin N SVT, Ioff N SVT, Idsat P SVT", 1,
     "기하(W/L) trend", "W trend", "M2_ET", "trend", "", "", "site"),
    (3, "L", "Vtlin N SVT, Vtsat N SVT, Vtlin P SVT", 2, "", "L trend",
     "M2_ET", "trend", "", "", "avg"),
    # 두 번째 리포트 — 콤보에서 갈아 끼워 본다
    (1, "Vtlin N LVT", "Idsat N LVT", 1, "LVT 평가", "LVT Idsat–Vt",
     "DEV_EVAL", "scatter", "", "", "site"),
    (1, "Jg Gate", "BVox", 2, "", "게이트 신뢰성(측정 없는 조건 포함)",
     "DEV_EVAL", "scatter", "", "", "site"),
    (1, "W", "Idsat N LVT, Jg Gate", 3, "", "W trend", "DEV_EVAL",
     "trend", "", "", "med"),
]

_TABLE_ROWS: list[tuple] = [
    # item_id, CAT1, CAT2, CAT3, CAT4, Report
    ("Idsat N SVT", "DC", "NMOS", "Idsat", "SVT", "M2_ET"),
    ("Idsat N LVT", "DC", "NMOS", "Idsat", "LVT", "M2_ET"),
    ("Vtlin N SVT", "DC", "NMOS", "Vt", "lin SVT", "M2_ET"),
    ("Vtsat N SVT", "DC", "NMOS", "Vt", "sat SVT", "M2_ET"),
    ("Vtlin N LVT", "DC", "NMOS", "Vt", "lin LVT", "M2_ET"),
    ("Idsat P SVT", "DC", "PMOS", "Idsat", "SVT", "M2_ET"),
    ("Vtlin P SVT", "DC", "PMOS", "Vt", "lin SVT", "M2_ET"),
    ("Ioff N SVT", "Leakage", "NMOS", "Ioff", "SVT", "M2_ET"),
    ("Ioff P SVT", "Leakage", "PMOS", "Ioff", "SVT", "M2_ET"),
    ("Jg Gate", "Leakage", "Gate", "Jg", "", "M2_ET"),
    ("Leak 합", "Leakage", "합계", "Sum", "", "M2_ET"),
    ("Idsat N/P", "Ratio", "N/P", "Idsat", "", "M2_ET"),
    ("N/P 여유", "Ratio", "N/P", "여유", "", "M2_ET"),
    ("Ioff decade", "Leakage", "Ioff", "decade", "", "M2_ET"),
    ("Vt delta N", "산포", "NMOS", "Vt", "sat−lin", "M2_ET"),
    ("Vt spread N", "산포", "NMOS", "Vt", "Std", "M2_ET"),
    ("Idsat 범위", "산포", "NMOS", "Idsat", "Max−Min", "M2_ET"),
    ("Rs Poly", "저항·용량", "Poly", "Rs", "", "M2_ET"),
    ("Cap MIM unit", "저항·용량", "MIM", "Cap", "", "M2_ET"),
    # DEV_EVAL — CAT3·CAT4를 비워 계층이 얕은 표도 함께 본다
    ("Idsat N LVT", "LVT", "NMOS", "", "", "DEV_EVAL"),
    ("Vtlin N LVT", "LVT", "NMOS", "", "", "DEV_EVAL"),
    ("Jg Gate", "신뢰성", "Gate", "", "", "DEV_EVAL"),
    ("BVox", "신뢰성", "Ox", "", "", "DEV_EVAL"),
]

PLOT_COLUMNS = ["page", "x", "y", "order", "title1", "title2",
                "Report", "Type", "x_name", "y_name", "Mode"]
TABLE_COLUMNS = ["item_id", "CAT1", "CAT2", "CAT3", "CAT4", "Report"]


def plot_frame() -> pl.DataFrame:
    cols: dict[str, pl.Series] = {}
    for i, name in enumerate(PLOT_COLUMNS):
        vals = [r[i] for r in _PLOT_ROWS]
        if name in ("page", "order"):
            cols[name] = pl.Series(name, vals, dtype=pl.Float64)
        else:
            cols[name] = pl.Series(name, [v or None for v in vals],
                                   dtype=pl.Utf8)
    return pl.DataFrame(cols)


def table_frame() -> pl.DataFrame:
    return pl.DataFrame({
        name: pl.Series(name, [r[i] or None for r in _TABLE_ROWS], dtype=pl.Utf8)
        for i, name in enumerate(TABLE_COLUMNS)})


# ── raw 측정값(long) ─────────────────────────────────────────
#: item별 step_seq — NMOS는 1, PMOS·누설·기타는 2에 기록된다(§10.1 병합 대상)
#: DC 특성은 seq 1, 누설·용량은 seq 2 — 읽을 때 한 점으로 합쳐야 x·y가 함께
#: 있는 행이 생긴다(page1 order2의 `Vtlin N SVT` vs `Ioff N SVT`가 그 예).
#: 데모 ADDP는 seq 안에서만 참조한다(seq를 넘나드는 수식은 테스트가 지킨다).
_SEQ = {
    "ET_IDSAT_N_SVT": 1, "ET_IDSAT_N_LVT": 1, "ET_VTLIN_N_SVT": 1,
    "ET_VTSAT_N_SVT": 1, "ET_VTLIN_N_LVT": 1, "ET_RS_POLY": 1,
    "ET_IDSAT_P_SVT": 1, "ET_VTLIN_P_SVT": 1,
    "ET_IOFF_N_SVT": 2, "ET_IOFF_P_SVT": 2, "ET_JG_GATE": 2,
    "ET_BVOX": 2, "ET_CAP_MIM": 2,
}


def _contexts() -> pl.DataFrame:
    """측정 조건 한 줄 = 한 측정점 (lot·wafer·die·step·온도·site)."""
    rows: list[dict] = []
    for plan in LOTS:
        for waf in plan.wafers:
            for step in plan.steps:
                for temp in plan.temps:
                    for x, y in plan.dies:
                        rows.append({
                            "line_id": LINE_ID, "root_lot_id": plan.lot,
                            "wafer_id": waf, "chip_x_pos": x, "chip_y_pos": y,
                            "temperature": temp, "step_id": step,
                            "total_site_cnt": plan.site_cnt,
                            "day": plan.day,
                        })
    return pl.DataFrame(rows)


def _values(ctx: pl.DataFrame, rng: np.random.Generator) -> dict[str, np.ndarray]:
    """ALIAS → 표시 단위 값 배열. 실험 조건·온도·step 효과를 담는다."""
    n = ctx.height
    bias_of = _bias_map()
    bias = np.array([bias_of.get((lot, waf), 0.0) for lot, waf
                     in zip(ctx["root_lot_id"], ctx["wafer_id"])])
    # wafer마다 고정 오프셋 — 같은 wafer의 점들이 뭉치게(현실의 wafer 산포)
    waf_seed = np.array([abs(hash((lot, waf))) % 997 for lot, waf
                         in zip(ctx["root_lot_id"], ctx["wafer_id"])])
    waf_off = (waf_seed / 997.0 - 0.5) * 0.03
    hot = (ctx["temperature"].to_numpy() > 60).astype(float)
    m3 = (ctx["step_id"].to_numpy() == "M3ET").astype(float)
    edge = (np.abs(ctx["chip_x_pos"].to_numpy())
            + np.abs(ctx["chip_y_pos"].to_numpy())) / 4.0     # 0(중앙)~1(가장자리)

    vt = (0.46 + 0.6 * bias + waf_off - 0.045 * hot + 0.012 * m3
          - 0.010 * edge + rng.normal(0, 0.020, n))
    vts = vt + 0.075 + rng.normal(0, 0.010, n)
    vt_lvt = vt - 0.16 + rng.normal(0, 0.015, n)
    ids = (0.60 + bias - (vt - 0.46) * 1.4 - 0.05 * hot
           + rng.normal(0, 0.040, n))
    idl = ids * 1.24 + rng.normal(0, 0.030, n)
    ioff = np.maximum(1e-3, 0.16 * 10 ** (-(vt - 0.46) * 6) * (1 + 4 * hot)
                      + rng.normal(0, 0.02, n))
    vtp = 0.47 - 0.4 * bias + waf_off + rng.normal(0, 0.018, n)
    idp = 0.27 - 0.25 * bias + rng.normal(0, 0.018, n)
    ioffp = np.maximum(1e-3, 0.22 * 10 ** (-(vtp - 0.47) * 5) * (1 + 3 * hot)
                       + rng.normal(0, 0.03, n))
    jg = np.maximum(1e-4, 0.9 + 2.5 * edge + rng.normal(0, 0.25, n))
    rs = 220 + 40 * bias + rng.normal(0, 9, n)
    bvox = 6.2 + rng.normal(0, 0.25, n)
    cap = 1.84 + rng.normal(0, 0.02, n)

    return {"Idsat N SVT": ids, "Idsat N LVT": idl, "Vtlin N SVT": vt,
            "Vtsat N SVT": vts, "Vtlin N LVT": vt_lvt, "Ioff N SVT": ioff,
            "Idsat P SVT": idp, "Vtlin P SVT": vtp, "Ioff P SVT": ioffp,
            "Jg Gate": jg, "Rs Poly": rs, "BVox": bvox, "Cap MIM unit": cap}


def long_frame(seed: int = SEED) -> pl.DataFrame:
    """추출 결과(`extractor.ARROW_SCHEMA`)와 **같은 스키마**의 raw long 프레임.

    이 프레임은 그대로 `reformatter.apply()` → `db.pivot_and_load()`에 넣을 수
    있다. 즉 bdq도 Excel도 없이 파이프라인 전체를 데모로 돌린다.
    """
    rng = np.random.default_rng(seed)
    ctx = _contexts()
    n = ctx.height
    vals = _values(ctx, rng)
    scale = {r[2]: float(r[4]) for r in RULE_ROWS if r[0] == "REAL"}
    #: 원본이 음수로 기록되는 항목 — 리포메터 ABSOLUTE가 되돌린다
    negative = {"Idsat P SVT", "Vtlin P SVT", "Ioff N SVT", "Ioff P SVT",
                "Jg Gate"}

    base = ctx.drop("day")
    day = ctx["day"].to_list()
    parts: list[pl.DataFrame] = []
    for itemid, alias in real_items():
        v = np.asarray(vals[alias], dtype=float) / scale[alias]
        if alias in negative:
            v = -v
        keep = np.ones(n, dtype=bool)
        if alias == "Rs Poly":
            # 3%는 미측정(NULL), 1%는 0 — ADDP 분모 함정
            miss = rng.random(n) < 0.03
            zero = rng.random(n) < 0.01
            v = np.where(zero, 0.0, v)
            v = np.where(miss, np.nan, v)
        if alias == "BVox":
            keep = (ctx["step_id"].to_numpy() == "M3ET")      # 그 step에서만 측정
        if not keep.any():
            continue
        seq = _SEQ[itemid]
        t0 = [datetime.combine(d, datetime.min.time())
              + timedelta(hours=9, minutes=17 * seq) for d in day]
        part = base.with_columns(
            step_seq=pl.lit(seq, dtype=pl.Int32),
            tkout_time=pl.Series("tkout_time", t0, dtype=pl.Datetime("us")),
            item_id=pl.lit(itemid),
            et_value=pl.Series("et_value", v, dtype=pl.Float64),
        )
        parts.append(part.filter(pl.Series(keep)) if not keep.all() else part)

    long = pl.concat(parts, how="vertical")

    # retest — 같은 키에 **더 이른** 시각·나쁜 값을 한 벌 더 넣는다.
    # 읽을 때 QUALIFY가 최신만 남기므로 그림에는 나타나지 않아야 한다.
    old = (long.filter((pl.col("root_lot_id") == "PA123")
                       & (pl.col("wafer_id").is_in(["W01", "W02"]))
                       & (pl.col("step_id") == "M2ET"))
           .with_columns(tkout_time=pl.col("tkout_time") - pl.duration(hours=3),
                         et_value=pl.col("et_value") * 0.55))
    long = pl.concat([old, long], how="vertical")

    return _cast_long(long)


def _cast_long(df: pl.DataFrame) -> pl.DataFrame:
    """추출 스키마와 컬럼·타입·순서를 맞춘다(NaN은 NULL로)."""
    from etreport.data.extractor import ARROW_SCHEMA

    order = [f.name for f in ARROW_SCHEMA]
    df = df.with_columns(
        pl.col("et_value").fill_nan(None),
        pl.col("chip_x_pos").cast(pl.Int32), pl.col("chip_y_pos").cast(pl.Int32),
        pl.col("total_site_cnt").cast(pl.Int32),
        pl.col("temperature").cast(pl.Float64))
    return df.select(order)


# ── 분석 프레임(wide) ────────────────────────────────────────
def wide_frame(long: pl.DataFrame | None = None,
               rf: Reformatter | None = None) -> pl.DataFrame:
    """long → 분석 화면이 쓰는 wide 프레임(`loader.RESERVED` + ALIAS들).

    DuckDB를 거치지 않는 대신 **읽기 경로와 같은 규칙**을 따른다: 온도를 5단위로
    보정하고, retest는 최신 시각만 남기고, step_seq만 다른 행은 한 점으로 합친다.
    """
    from etreport.data import reformatter as R
    from etreport.data.extractor import correct_temperature

    rf = rf or reformatter()
    long = long_frame() if long is None else long
    long = correct_temperature(long)
    applied = R.apply(rf, long)          # 스케일·절대값·ALIAS·ADDP (실제 코드)

    key = ["root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos",
           "temperature", "step_id", "total_site_cnt"]
    # retest: 같은 (키 + step_seq + item)에서 가장 최신 시각만
    applied = (applied.sort("tkout_time")
               .group_by([*key, "step_seq", "item_id"], maintain_order=True)
               .last())
    wide = applied.pivot(on="item_id", index=key, values="value",
                         aggregate_function="first")

    return (wide
            .with_columns(
                key=pl.concat_str([pl.col(c).cast(pl.Utf8).fill_null("_")
                                   for c in key], separator="|").hash()
                .cast(pl.Utf8),
                gid=pl.lit(""))
            .rename({"root_lot_id": "lot", "wafer_id": "wafer",
                     "step_id": "step", "temperature": "temp",
                     "total_site_cnt": "site"})
            .drop("chip_x_pos", "chip_y_pos")
            .select(["key", "lot", "wafer", "gid", "step", "temp", "site",
                     *[r.alias for r in rf.rules]]))


# ── 사내 소스 3종의 가짜 응답 ────────────────────────────────
def metrology_frame(lots: list[str] | None = None,
                    seed: int = SEED + 1) -> pl.DataFrame:
    """`fab.f_fab_wf_met` 모양의 inline 계측 — 컬럼명은 원본 그대로.

    subitem 규칙(site = 요약 subitem 제외 · wafer = Q2)을 확인할 수 있도록
    site 값(S1~S5)과 요약값(Q2·STD·MIN·MAX)을 함께 넣는다.
    """
    rng = np.random.default_rng(seed)
    want = set(lots or [p.lot for p in LOTS])
    bias_of = _bias_map()
    rows: list[dict] = []
    steps = {"P100": ("CD_ISO", "CD_DENSE"), "E200": ("THK_OX",),
             "M300": ("RCS_M1",)}
    for plan in LOTS:
        if plan.lot not in want:
            continue
        for waf in plan.wafers:
            b = bias_of.get((plan.lot, waf), 0.0)
            for step, items in steps.items():
                for item in items:
                    center = {"CD_ISO": 42.0, "CD_DENSE": 38.5,
                              "THK_OX": 21.4, "RCS_M1": 8.6}[item]
                    site_vals = center * (1 + 0.9 * b) + rng.normal(0, 0.3, 5)
                    for i, v in enumerate(site_vals, 1):
                        rows.append({"root_lot_id": plan.lot, "wafer_id": waf,
                                     "step_id": step, "item_id": item,
                                     "subitem_id": f"S{i}", "fab_value": float(v)})
                    for sub, v in (("Q2", float(np.median(site_vals))),
                                   ("STD", float(site_vals.std(ddof=1))),
                                   ("MIN", float(site_vals.min())),
                                   ("MAX", float(site_vals.max()))):
                        rows.append({"root_lot_id": plan.lot, "wafer_id": waf,
                                     "step_id": step, "item_id": item,
                                     "subitem_id": sub, "fab_value": v})
    df = pl.DataFrame(rows)
    return df.with_columns(
        line_id=pl.lit(LINE_ID),
        tkout_time=pl.lit(datetime(2026, 8, 2, 6, 30), dtype=pl.Datetime("us")))


def tracking_frame(lots: list[str] | None = None) -> pl.DataFrame:
    """`fab.f_fab_tracking` 모양 — PHOTO는 reticle, 그 외는 ppid로 갈린다.

    실험이 걸린 step(M1·M5·M8)만 조건이 wafer마다 다르고 나머지는 같다.
    `split_steps()`가 갈리는 step만 factor로 올리는지 확인할 수 있다.
    """
    want = set(lots or [p.lot for p in LOTS])
    sp = split_frame()
    codes = {(r["lot"], r["wafer"]): r for r in sp.iter_rows(named=True)}
    plan = [                      # (step_seq, step_id, area, 갈리는 실험 컬럼)
        (100, "P100", "PHOTO", "M1"),
        (200, "E200", "ETCH", None),
        (300, "I300", "IMP", "M5"),
        (400, "M400", "METAL", None),
        (500, "P500", "PHOTO", "M8"),
    ]
    rows: list[dict] = []
    for lp in LOTS:
        if lp.lot not in want:
            continue
        for waf in lp.wafers:
            c = codes.get((lp.lot, waf))
            for seq, step, area, factor in plan:
                code = (c or {}).get(factor) if factor else None
                tag = (code or "STD").replace("_", "")
                rows.append({
                    # step_seq가 step 식별자다(`fabtracking.step_key_expr`, §3)
                    "part_id": "8NM-SRAM", "process_id": step,
                    "step_seq": seq, "root_lot_id": lp.lot, "wafer_id": waf,
                    "area": area,
                    "tkout_time": datetime.combine(
                        lp.day - timedelta(days=3), datetime.min.time()),
                    "foup_id": f"FP{seq:03d}", "eqp_model": "MDL",
                    "eqp_id": f"EQ{seq:03d}", "unit_id": "U1",
                    "chamber_id": "C1",
                    "ppid": f"{step}_{tag}" if area != "PHOTO" else f"{step}_STD",
                    "reticle_id": f"RT_{tag}" if area == "PHOTO" else "RT_STD",
                    "ein_ecn_no": "ECN-2026-0815", "line_id": LINE_ID})
    return pl.DataFrame(rows)
