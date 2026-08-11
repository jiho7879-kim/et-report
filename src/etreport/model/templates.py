"""템플릿 로더.

plot  : page · x · y · order · title1 · title2 · Report · Type · x_name · y_name
        order = 1~6, 왼→오 (1·2·3 윗줄 / 4·5·6 아랫줄)
table : item_id · CAT1 · CAT2 · CAT3 · Report   (item_id ≡ 리포메터 ALIAS)

Report 컬럼이 두 템플릿의 공통 키 — 한 파일에 여러 리포트를 담고 UI에서 고른다.
검증 오류는 (행 번호, 메시지)로 모아 UI에 일괄 표시한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from etreport.data.reformatter import Reformatter
from etreport.model.specs import PageSpec, PlotSpec, ReportSpec, TableRowSpec

PLOT_COLS = ["page", "x", "y", "order", "title1", "title2",
             "Report", "Type", "x_name", "y_name"]


def _with_rowno(df: pl.DataFrame) -> pl.DataFrame:
    """엑셀 행 번호(_row, 헤더가 1행이므로 2부터)를 컬럼으로 붙인다."""
    return df.with_row_index("_row", offset=2)


def _int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default
TBL_COLS = ["item_id", "CAT1", "CAT2", "CAT3", "Report"]


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
    t = Templates()
    if Path(plot_path).resolve() == Path(table_path).resolve():
        a, b = read_sheets(plot_path, [plot_sheet, table_sheet])
    else:
        a = read_sheet(plot_path, plot_sheet)
        b = read_sheet(table_path, table_sheet)
    t.plot_rows = _with_rowno(a)
    t.table_rows = _with_rowno(b)
    t.plot_source = (plot_path, plot_sheet)
    t.table_source = (table_path, table_sheet)
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
            typ = str(r["Type"] or "scatter").lower()
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
            xs = [s.strip() for s in str(r["x"] or "").split(",") if s.strip()]
            ys = [s.strip() for s in str(r["y"] or "").split(",") if s.strip()]
            if len(xs) != len(ys) and min(len(xs), len(ys)) != 1:
                t.warnings.append(TemplateError(
                    "plot", i,
                    f"x {len(xs)}개·y {len(ys)}개 — 쉼표 개수가 달라 건너뜁니다"))
                t.skip_plot.add(i)
                continue
            bad = [a for a in (*xs, *ys) if a not in aliases]
            if bad:
                t.warnings.append(TemplateError(
                    "plot", i,
                    f"계산 불가 ALIAS {', '.join(bad)} — 이 plot을 건너뜁니다"))
                t.skip_plot.add(i)

    df = t.table_rows
    miss = [c for c in TBL_COLS if df is None or c not in df.columns]
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
            title1 = next((str(v) for v in prow["title1"] if v), f"Page {page_no}")
            page = PageSpec(number=page_no, title=title1)
            for r in prow.iter_rows(named=True):
                if int(r["_row"]) in t.skip_plot:
                    continue
                idx = max(0, min(5, _int(r["order"], 1) - 1))
                typ = str(r["Type"] or "scatter").lower()
                page.slots[idx] = PlotSpec(
                    title=str(r["title2"] or ""),
                    x=str(r["x"] or ""), y=str(r["y"] or ""),
                    x_name=str(r["x_name"] or ""), y_name=str(r["y_name"] or ""),
                    type=typ,
                )
            if any(page.slots):
                spec.pages.append(page)

    if t.table_rows is not None:
        for r in t.table_rows.iter_rows(named=True):
            if int(r["_row"]) in t.skip_table \
                    or str(r.get("Report") or "") != report:
                continue
            spec.table_rows.append(TableRowSpec(
                item_id=str(r["item_id"] or ""),
                cat1=str(r["CAT1"] or ""),
                cat2=str(r["CAT2"] or ""),
                cat3=str(r["CAT3"] or ""),
            ))
    return spec
