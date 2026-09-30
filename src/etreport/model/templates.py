"""템플릿 로더.

plot  : page · x · y · order · title1 · title2 · Report · Type · x_name · y_name
        · spec(선택: global | functional)
        order = 1~6, 왼→오 (1·2·3 윗줄 / 4·5·6 아랫줄)
table : item_id · CAT1 · CAT2 · CAT3 · Report   (item_id ≡ 리포메터 ALIAS)

Report 컬럼이 두 템플릿의 공통 키 — 한 파일에 여러 리포트를 담고 UI에서 고른다.
검증 오류는 (행 번호, 메시지)로 모아 UI에 일괄 표시한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from etreport.data.reformatter import Reformatter
from etreport.model.categories import BUILTIN
from etreport.model.specs import (
    CAT_PLOTS,
    GEOM_COLUMNS,
    PageSpec,
    PlotSpec,
    ReportSpec,
    TableRowSpec,
)

PLOT_COLS = ["page", "x", "y", "order", "title1", "title2",
             "Report", "Type", "x_name", "y_name"]


def normalize_plot_type(value) -> str:
    """사용자가 흔히 쓰는 plot 종류 표기를 내부 타입 하나로 맞춘다.

    템플릿의 범주축은 리포메터 ALIAS가 아니므로, 여기서 정규화가 빠지면
    ``boxplot``/``bar chart`` 행이 산점도로 오인되어 통째로 skip된다.
    """
    raw = str(value or "scatter").strip().lower()
    compact = re.sub(r"[\s_-]+", "", raw)
    return {
        "boxplot": "box", "box": "box",
        "barchart": "bar", "bar": "bar",
        "scatterplot": "scatter", "scatter": "scatter",
        "wltrend": "trend", "trend": "trend",
    }.get(compact, raw)


def _row_type(r: dict) -> str:
    """템플릿 한 행의 plot 종류 — 검증과 조립이 같은 판정을 쓴다.

    Type을 비우거나 scatter로 둔 채 x에 `wafer`·`lot+wafer` 같은 기본 범주를
    적으면 box로 읽는다. 범주는 리포메터 ALIAS가 아니라서 산점도로 보면
    "계산 불가 ALIAS"로 그 plot이 통째로 빠진다(page에 그것뿐이면 page까지).
    """
    typ = normalize_plot_type(r["Type"])
    if typ == "scatter" and str(r["x"] or "").strip() in BUILTIN:
        return "box"
    return typ


def _with_rowno(df: pl.DataFrame) -> pl.DataFrame:
    """엑셀 행 번호(_row, 헤더가 1행이므로 2부터)를 컬럼으로 붙인다."""
    return df.with_row_index("_row", offset=2)


def _int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default
#: table 시트의 표준 컬럼(예시·되쓰기용).
TBL_COLS = ["item_id", "CAT1", "CAT2", "CAT3", "Report"]
#: 실제로 **있어야만 하는** 컬럼. CAT2 이후는 있으면 쓰고 없으면 그만이다 —
#: 개수를 고정하면 CAT4·CAT5를 쓰는 템플릿을 못 받는다(§3.3 확정).
TBL_REQUIRED = ["item_id", "CAT1", "Report"]
_CAT_RE = re.compile(r"^\s*CAT\s*(\d+)\s*$", re.IGNORECASE)


def cat_columns(df: pl.DataFrame | None) -> list[str]:
    """시트에서 `CAT1, CAT2, …`를 찾아 **번호순**으로. 개수는 고정하지 않는다."""
    if df is None:
        return []
    found = [(int(m[1]), c) for c in df.columns if (m := _CAT_RE.match(str(c)))]
    return [c for _n, c in sorted(found)]


@dataclass
class TemplateError:
    sheet: str
    row: int
    message: str


@dataclass
class Templates:
    plot_rows: pl.DataFrame | None = None
    table_rows: pl.DataFrame | None = None
    errors: list[TemplateError] = field(default_factory=list)     # 치명적
    warnings: list[TemplateError] = field(default_factory=list)   # 건너뛴 행
    skip_plot: set[int] = field(default_factory=set)              # 1-based 행
    skip_table: set[int] = field(default_factory=set)
    plot_source: tuple = ()      # (경로, 시트) — UI 수정 내용을 되쓸 때 사용
    table_source: tuple = ()

    def report_lines(self) -> list[str]:
        return [f"[{e.sheet}] {e.row}행: {e.message}"
                for e in (*self.errors, *self.warnings)]

    def reports(self) -> list[str]:
        names: list[str] = []
        for df in (self.plot_rows, self.table_rows):
            if df is not None and "Report" in df.columns:
                for v in df["Report"].drop_nulls():
                    if v not in names:
                        names.append(str(v))
        return names


def load(plot_path: str, table_path: str, rf: Reformatter,
         plot_sheet: str | int = 0, table_sheet: str | int = 0) -> Templates:
    from etreport.data.xlio import read_sheet, read_sheets
    if Path(plot_path).resolve() == Path(table_path).resolve():
        a, b = read_sheets(plot_path, [plot_sheet, table_sheet])
    else:
        a = read_sheet(plot_path, plot_sheet)
        b = read_sheet(table_path, table_sheet)
    t = from_frames(a, b, rf)
    t.plot_source = (plot_path, plot_sheet)
    t.table_source = (table_path, table_sheet)
    return t


def from_frames(plot_df: pl.DataFrame, table_df: pl.DataFrame,
                rf: Reformatter) -> Templates:
    """이미 읽어 둔 표 두 장 → **검증까지 마친** Templates.

    파일을 거치지 않는 입구다(데모·테스트). 검증은 load()와 같은 코드를
    타므로 "파일로 읽었을 때만 걸리는 오류"가 생기지 않는다.
    """
    # `spec`은 선택 열이라 기존 템플릿을 강제 변환하지 않는다. 다만 Excel에서
    # `SPEC`처럼 대문자로 적어도 한 이름으로 읽어야 한다.
    for col in plot_df.columns:
        if str(col).strip().lower() == "spec" and col != "spec":
            plot_df = plot_df.rename({col: "spec"})
            break
    t = Templates()
    t.plot_rows = _with_rowno(plot_df)
    t.table_rows = _with_rowno(table_df)
    _validate(t, rf)
    return t


def _validate(t: Templates, rf: Reformatter) -> None:
    aliases = set(rf.by_alias)

    df = t.plot_rows
    miss = [c for c in PLOT_COLS if df is None or c not in df.columns]
    if miss:
        t.errors.append(TemplateError("plot", 0, f"컬럼 누락: {', '.join(miss)}"))
    elif df is not None:
        seen: set[tuple] = set()
        for r in df.iter_rows(named=True):
            i = int(r["_row"])
            typ = _row_type(r)
            key = (r["Report"], r["page"], r["order"])
            if key in seen:
                t.warnings.append(TemplateError(
                    "plot", i,
                    f"page {r['page']} order {r['order']} 중복 — 뒤 행이 이깁니다"))
            seen.add(key)
            if not (1 <= _int(r["order"]) <= 6):
                t.warnings.append(TemplateError(
                    "plot", i, "order가 1~6이 아니어서 이 행을 건너뜁니다"))
                t.skip_plot.add(i)
                continue
            if typ == "table":
                continue
            mode = str(r.get("Mode") or "site").strip().lower()
            if mode not in ("site", "avg", "med", "std"):
                t.warnings.append(TemplateError(
                    "plot", i, "Mode 값이 잘못되어 site로 처리합니다"))
            xs = [s.strip() for s in str(r["x"] or "").split(",") if s.strip()]
            ys = [s.strip() for s in str(r["y"] or "").split(",") if s.strip()]
            if len(xs) != len(ys) and min(len(xs), len(ys)) != 1:
                t.warnings.append(TemplateError(
                    "plot", i,
                    f"x {len(xs)}개·y {len(ys)}개 — 쉼표 개수가 달라 건너뜁니다"))
                t.skip_plot.add(i)
                continue
            if typ == "trend":
                # x는 기하(W/L) 단일 값 — alias 검사 제외
                xv = str(r["x"] or "").strip()
                if xv not in GEOM_COLUMNS:
                    t.warnings.append(TemplateError(
                        "plot", i, "trend의 x는 W 또는 L이어야 합니다"))
                    t.skip_plot.add(i)
                    continue
                bad = [a for a in ys if a not in aliases]
            elif typ in CAT_PLOTS:
                # x는 **범주 이름**이다(lot+wafer·gid·step·온도·tracking 컬럼…).
                # 리포메터 ALIAS가 아니므로 여기서 검사할 수 없다 — 실제로 그 이름의
                # 컬럼이 있는지는 데이터를 읽은 뒤에야 알 수 있고, 없으면 렌더러가
                # "그릴 값이 없습니다"로 표시한다. y만 검사한다.
                if not str(r["x"] or "").strip():
                    t.warnings.append(TemplateError(
                        "plot", i, f"{typ}의 x(범주)가 비어 있어 건너뜁니다"))
                    t.skip_plot.add(i)
                    continue
                bad = [a for a in ys if a not in aliases]
            else:
                bad = [a for a in (*xs, *ys) if a not in aliases]
            if bad:
                t.warnings.append(TemplateError(
                    "plot", i,
                    f"계산 불가 ALIAS {', '.join(bad)} — 이 plot을 건너뜁니다"))
                t.skip_plot.add(i)

    df = t.table_rows
    miss = [c for c in TBL_REQUIRED if df is None or c not in df.columns]
    if miss:
        t.errors.append(TemplateError("table", 0, f"컬럼 누락: {', '.join(miss)}"))
    elif df is not None:
        for r in df.iter_rows(named=True):
            i = int(r["_row"])
            a = str(r["item_id"] or "").strip()
            if not a or a not in aliases:
                t.warnings.append(TemplateError(
                    "table", i,
                    f"계산 불가 ALIAS '{a}' — 이 행을 건너뜁니다" if a
                    else "item_id가 비어 있어 건너뜁니다"))
                t.skip_table.add(i)
                continue
            if not str(r["CAT1"] or "").strip():
                t.warnings.append(TemplateError(
                    "table", i, "CAT1이 비어 있어 건너뜁니다"))
                t.skip_table.add(i)


def build_report(t: Templates, report: str) -> ReportSpec:
    """Report 이름 하나에 대한 PageSpec/TableRowSpec 조립."""
    spec = ReportSpec(report=report)

    if t.plot_rows is not None:
        rows = t.plot_rows.filter(pl.col("Report") == report)
        for page_no in sorted({_int(p) for p in rows["page"].drop_nulls()}):
            prow = rows.filter(pl.col("page").cast(pl.Float64,
                                                   strict=False) == page_no)
            # 사용자가 주로 보는 title1은 plot 제목, title2는 page 제목이다.
            # 이전 구현은 둘을 반대로 읽어 템플릿과 화면의 역할이 바뀌었다.
            title2 = next((str(v) for v in prow["title2"] if v), f"Page {page_no}")
            page = PageSpec(number=page_no, title=title2)
            for r in prow.iter_rows(named=True):
                if int(r["_row"]) in t.skip_plot:
                    continue
                idx = max(0, min(5, _int(r["order"], 1) - 1))
                typ = _row_type(r)
                mode = str(r.get("Mode") or "site").strip().lower()
                if mode not in ("site", "avg", "med", "std"):
                    mode = "site"
                page.slots[idx] = PlotSpec(
                    title=str(r["title1"] or ""),
                    x=str(r["x"] or ""), y=str(r["y"] or ""),
                    x_name=str(r["x_name"] or ""), y_name=str(r["y_name"] or ""),
                    type=typ,
                    mode=mode,
                    spec=str(r.get("spec") or "").strip().lower(),
                )
            if any(page.slots):
                spec.pages.append(page)

    if t.table_rows is not None:
        cats = cat_columns(t.table_rows)          # CAT1, CAT2, … 번호순 (개수 자유)
        spec.cat_names = [c.upper().replace(" ", "") for c in cats[1:]]
        for r in t.table_rows.iter_rows(named=True):
            if int(r["_row"]) in t.skip_table \
                    or str(r.get("Report") or "") != report:
                continue
            spec.table_rows.append(TableRowSpec(
                item_id=str(r["item_id"] or ""),
                cats=[str(r[c] or "").strip() for c in cats],
            ))
    return spec
