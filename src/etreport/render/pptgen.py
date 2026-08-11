"""PPT 생성 — 2×3 슬롯, 실험별 반복, 표 자동 삽입, 제외 이력 슬라이드.

표 슬라이드 정책(확정):
  pptx는 슬라이드 크기가 **프레젠테이션 전역**이라 장마다 다르게 못 준다.
  - wide  : 덱 전체 폭을 wafer 수에 맞춰 확장(기본). 글자 9pt 하한 유지.
  - split : 16:9 유지, wafer 12장씩 분할(제목에 1/N), 라벨 열 매 장 반복.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import polars as pl
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

from etreport.data.reformatter import Reformatter
from etreport.model.specs import PageSpec, ReportSpec, fmt_value
from etreport.render import mpl_renderer

BASE_W_IN = 13.333
BASE_H_IN = 7.5
MARGIN_IN = 0.35
TITLE_H_IN = 0.62
WAFERS_PER_SLIDE = 12         # split 모드
FONT_MIN = Pt(9)
SPEC_FILL = RGBColor(0xFF, 0xEC, 0xEE)
SPEC_TXT = RGBColor(0xD7, 0x00, 0x15)


@dataclass
class TableData:
    """export.summary가 만들어 주는 표 하나 분량."""
    name: str                                  # CAT1
    header_lots: list[tuple[str, list[str]]]   # [(lot, [wafer,…]), …]
    rows: list[dict]                           # cat2, cat3, item, values, offspec


def deck_width_in(n_wafers: int, mode: str) -> float:
    if mode != "wide":
        return BASE_W_IN
    need = 2.8 + 0.75 * n_wafers               # 라벨 3열 + wafer당 0.75"
    return max(BASE_W_IN, min(need, 56.0))     # pptx 최대 56"


def build_deck(
    report: ReportSpec,
    experiments: list[str],                    # factor 이름들. 반복 없으면 [""]
    group_styles_of,                           # exp → list[GroupStyle]
    plot_data_of,                              # (exp, PlotSpec) → {gid: DataFrame}
    tables_of,                                 # exp → list[TableData]
    rf: Reformatter,
    log_patterns: list[str],
    exclusion_log: pl.DataFrame,
    table_mode: str = "wide",
    n_wafers: int = 0,
) -> Presentation:
    prs = Presentation()
    prs.slide_width = Inches(deck_width_in(n_wafers, table_mode))
    prs.slide_height = Inches(BASE_H_IN)
    blank = prs.slide_layouts[6]

    for exp in experiments:
        styles = group_styles_of(exp)
        for page in report.pages:
            slide = prs.slides.add_slide(blank)
            suffix = f" — {exp}" if exp else ""
            _add_title(slide, prs, page.title + suffix)
            _fill_slots(slide, prs, page, exp, styles,
                        plot_data_of, tables_of, rf, log_patterns)

    _exclusion_slide(prs, blank, exclusion_log)
    return prs


# ── 내부 ─────────────────────────────────────────────────────
def _add_title(slide, prs, text: str) -> None:
    tb = slide.shapes.add_textbox(
        Inches(MARGIN_IN), Inches(0.18),
        prs.slide_width - Inches(2 * MARGIN_IN), Inches(TITLE_H_IN))
    p = tb.text_frame.paragraphs[0]
    p.text = text
    p.font.size = Pt(20)
    p.font.bold = True
    # 제목 밑줄 — XML 직접 삽입 대신 얇은 사각형
    ln = slide.shapes.add_shape(
        1, Inches(MARGIN_IN), Inches(0.18 + TITLE_H_IN),
        prs.slide_width - Inches(2 * MARGIN_IN), Emu(19050))
    ln.fill.solid()
    ln.fill.fore_color.rgb = RGBColor(0x1D, 0x1D, 0x1F)
    ln.line.fill.background()


def _slot_rect(prs, idx: int):
    """order-1 (0~5) → (left, top, w, h). 1·2·3 윗줄 / 4·5·6 아랫줄, 왼→오."""
    top0 = Inches(0.18 + TITLE_H_IN + 0.12)
    W = prs.slide_width - Inches(2 * MARGIN_IN)
    H = prs.slide_height - top0 - Inches(MARGIN_IN)
    gw, gh = W / 3, H / 2
    r, c = divmod(idx, 3)
    pad = Emu(45720)
    return (Inches(MARGIN_IN) + c * gw + pad, top0 + r * gh + pad,
            gw - 2 * pad, gh - 2 * pad)


def _fill_slots(slide, prs, page: PageSpec, exp, styles,
                plot_data_of, tables_of, rf, log_patterns) -> None:
    table_iter = iter(tables_of(exp))
    for i, spec in enumerate(page.slots):
        if spec is None:
            continue
        left, top, w, h = _slot_rect(prs, i)
        if spec.type == "table":
            td = next(table_iter, None)
            if td:
                _mini_table(slide, td, left, top, w, h)
            continue
        figsize = (w / 914400, h / 914400)        # EMU → inch
        fig = mpl_renderer.render(spec, plot_data_of(exp, spec),
                                  styles, rf, log_patterns, figsize)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150)
        buf.seek(0)
        pic = slide.shapes.add_picture(buf, left, top, width=w, height=h)
        pic.line.color.rgb = RGBColor(0xD2, 0xD2, 0xD7)
        pic.line.width = Pt(0.75)


def _mini_table(slide, td: TableData, left, top, w, h) -> None:
    """슬롯 안 요약 표 — 열이 많으면 값 열만 균등 축소, 글꼴 9pt 하한."""
    wafer_n = sum(len(ws) for _, ws in td.header_lots)
    cols = 3 + wafer_n
    rows = 2 + len(td.rows)
    shape = slide.shapes.add_table(rows, cols, left, top, w, h)
    tbl = shape.table
    # 헤더
    hdr = ["CAT2", "CAT3", "item"] + [wf for _, ws in td.header_lots for wf in ws]
    lot_row = ["", "", ""] + [lot for lot, ws in td.header_lots for _ in ws]
    for c, txt in enumerate(lot_row):
        tbl.cell(0, c).text = txt
    for c, txt in enumerate(hdr):
        tbl.cell(1, c).text = txt
    # lot 헤더 가로 병합
    c = 3
    for _lot, ws in td.header_lots:
        if len(ws) > 1:
            tbl.cell(0, c).merge(tbl.cell(0, c + len(ws) - 1))
        c += len(ws)
    # 본문 + 규격 이탈 칠하기
    for r, row in enumerate(td.rows, start=2):
        tbl.cell(r, 0).text = row["cat2"]
        tbl.cell(r, 1).text = row["cat3"]
        tbl.cell(r, 2).text = row["item"]
        for c, (v, off) in enumerate(zip(row["values"], row["offspec"]), start=3):
            cell = tbl.cell(r, c)
            cell.text = fmt_value(v)
            if off:
                cell.fill.solid()
                cell.fill.fore_color.rgb = SPEC_FILL
    # 글꼴 일괄 — 9pt 하한
    for r in range(rows):
        for c in range(cols):
            for p in tbl.cell(r, c).text_frame.paragraphs:
                p.font.size = FONT_MIN
                if r == 2 and c >= 3:
                    pass
    # CAT2 세로 병합
    r0, prev = 2, td.rows[0]["cat2"] if td.rows else None
    for r in range(3, rows + 1):
        cur = td.rows[r - 2]["cat2"] if r - 2 < len(td.rows) else None
        if cur != prev:
            if r - 1 > r0:
                tbl.cell(r0, 0).merge(tbl.cell(r - 1, 0))
            r0, prev = r, cur


def split_table(td: TableData, per: int = WAFERS_PER_SLIDE) -> list[TableData]:
    """split 모드: wafer per장씩 끊어 TableData 여러 개로 (제목에 1/N)."""
    flat = [(lot, wf) for lot, ws in td.header_lots for wf in ws]
    if len(flat) <= per:
        return [td]
    parts: list[TableData] = []
    n = (len(flat) + per - 1) // per
    for k in range(n):
        seg = flat[k * per:(k + 1) * per]
        lots: list[tuple[str, list[str]]] = []
        for lot, wf in seg:
            if lots and lots[-1][0] == lot:
                lots[-1][1].append(wf)
            else:
                lots.append((lot, [wf]))
        idx = [flat.index(x) for x in seg]
        rows = [{**r,
                 "values": [r["values"][i] for i in idx],
                 "offspec": [r["offspec"][i] for i in idx]} for r in td.rows]
        parts.append(TableData(f"{td.name}  ({k + 1}/{n})", lots, rows))
    return parts


def _exclusion_slide(prs, layout, log: pl.DataFrame) -> None:
    s = prs.slides.add_slide(layout)
    _add_title(s, prs, f"제외 포인트 이력 — {len(log)}건")
    tb = s.shapes.add_textbox(Inches(MARGIN_IN), Inches(1.1),
                              prs.slide_width - Inches(2 * MARGIN_IN),
                              prs.slide_height - Inches(1.5))
    tf = tb.text_frame
    tf.word_wrap = True
    if log.is_empty():
        tf.paragraphs[0].text = "제외된 포인트 없음"
        tf.paragraphs[0].font.size = Pt(12)
        return
    for row in log.head(40).iter_rows(named=True):
        p = tf.add_paragraph()
        at = row.get("created_at")
        at = at.strftime("%m-%d %H:%M") if hasattr(at, "strftime") else str(at or "")
        p.text = f"{at}  {str(row['key_hash'])[:12]}…  {row.get('reason') or ''}"
        p.font.size = Pt(10)
    if len(log) > 40:
        p = tf.add_paragraph()
        p.text = f"… 외 {len(log) - 40}건 (전체는 세션 파일 참조)"
        p.font.size = Pt(10)
