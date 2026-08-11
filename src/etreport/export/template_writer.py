"""UI에서 편집한 리포트 구성을 plot 템플릿 엑셀에 되쓴다.

원본 시트의 컬럼 순서와 다른 Report 행들은 그대로 두고, 현재 Report에
해당하는 행만 화면 상태로 교체한다. 쓰기도 xlwings만 사용하며 .bak 사본을
남긴다(xlio.write_sheet).
"""
from __future__ import annotations

import logging

import polars as pl

from etreport.model.specs import ReportSpec

log = logging.getLogger(__name__)

NUMERIC_COLS = {"page", "order"}      # 되쓸 때 숫자 성질을 유지할 열


def rows_from_report(spec: ReportSpec) -> list[dict]:
    """ReportSpec → plot 템플릿 행들 (order = 슬롯 위치 1~6)."""
    out: list[dict] = []
    for page in spec.pages:
        first = True
        for i, s in enumerate(page.slots):
            if s is None:
                continue
            out.append({
                "page": page.number,
                "x": s.x,
                "y": s.y,
                "order": i + 1,
                "title1": page.title if first else "",
                "title2": s.title,
                "Report": spec.report,
                "Type": s.type,
                "x_name": s.x_name,
                "y_name": s.y_name,
                "Mode": s.mode,
            })
            first = False
    return out


def merged_frame(original: pl.DataFrame, spec: ReportSpec) -> pl.DataFrame:
    """다른 Report 행은 보존하고 현재 Report 행만 교체한 전체 시트."""
    cols = [c for c in original.columns if c != "_row"]
    keep = original.filter(
        pl.col("Report").cast(pl.Utf8) != spec.report).drop("_row", strict=False)
    new = pl.DataFrame(rows_from_report(spec))
    if new.is_empty():
        return keep.select(cols)
    # 원본에만 있는 부가 컬럼은 빈 값으로 채워 자리를 지킨다
    for c in cols:
        if c not in new.columns:
            new = new.with_columns(pl.lit(None).alias(c))
    new = new.select(cols)
    keep = keep.select(cols)
    # 타입 맞추기 — page·order 같은 숫자 열은 **숫자로 유지**한다.
    # 전부 문자열로 캐스팅하면 사용자 템플릿의 숫자가 텍스트로 바뀌어
    # 정렬·수식이 깨지고 엑셀이 '텍스트로 저장된 숫자' 경고를 띄운다.
    for c in cols:
        if c in NUMERIC_COLS:
            keep = keep.with_columns(pl.col(c).cast(pl.Float64, strict=False))
            new = new.with_columns(pl.col(c).cast(pl.Float64, strict=False))
        else:
            keep = keep.with_columns(pl.col(c).cast(pl.Utf8, strict=False))
            new = new.with_columns(pl.col(c).cast(pl.Utf8, strict=False))
    return pl.concat([keep, new], how="vertical")


def save_to_template(templates, spec: ReportSpec) -> str:
    """plot 템플릿 파일에 저장. 백업 파일 경로를 반환."""
    if not getattr(templates, "plot_source", None):
        raise RuntimeError("plot 템플릿 파일 경로를 알 수 없습니다 — 먼저 템플릿을 여세요")
    path, sheet = templates.plot_source
    df = merged_frame(templates.plot_rows, spec)
    from etreport.data.xlio import write_sheet
    bak = write_sheet(path, sheet, df)
    log.info("plot 템플릿 저장: %s [%s] · %d행", path, sheet, df.height)
    return bak


def summary(spec: ReportSpec) -> str:
    n = sum(1 for p in spec.pages for s in p.slots if s)
    return f"{spec.report} · {len(spec.pages)}페이지 · plot {n}개"
