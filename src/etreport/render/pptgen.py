"""PPT 생성 — 2×3 슬롯 plot 페이지, 표 전용 페이지, 제외 이력 슬라이드.

확정 사양(§7) 세 가지를 이 파일이 지킨다.

  §7.1 슬라이드는 **항상 16:9(13.333 × 7.5인치)**. pptx는 슬라이드 크기가
       프레젠테이션 전역이라, 표 때문에 덱을 넓히면 plot 페이지까지 가로로
       늘어진다. `slide_width`를 조건부로 바꾸지 말 것.
  §7.2 페이지 순서는 `plot 전부 → 표 전부 → 제외 이력`. 표는 (lot, wafer)별
       집계라 실험 구분과 무관하므로 **한 벌만** 만든다(실험마다 붙이면 중복).
  §7.3 표는 plot 템플릿과 무관한 **전용 페이지**로, CAT1마다 한 장.
       wafer가 많으면 두 가지 모드 —
         overflow(기본): 9pt를 지키고 표가 슬라이드 오른쪽 경계를 넘어가게 둔다
         split        : wafer 12장씩 나눠 여러 장으로 (제목에 1/N)
       **글자를 9pt 아래로 줄이지 않는다.** 뭉개지는 것보다 넘치는 게 낫다.

표 셀은 **반드시 병합 먼저, 값 나중**이다(§10.5) — 반대로 하면 python-pptx가
텍스트를 이어붙여 lot 헤더가 "PA1\\nPA1\\nPA1"이 된다.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import polars as pl
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml import parse_xml
from pptx.oxml.ns import nsdecls, qn
from pptx.util import Emu, Inches, Pt

from etreport.data.reformatter import Reformatter
from etreport.model.specs import PageSpec, ReportSpec, fmt_value
from etreport.render import mpl_renderer

BASE_W_IN = 13.333            # 16:9 고정 — §7.1, 조건부로 바꾸지 말 것
BASE_H_IN = 7.5
MARGIN_IN = 0.35
TITLE_H_IN = 0.62
WAFERS_PER_SLIDE = 12         # split 모드
FONT_MIN = Pt(9)              # 9pt 하한 — 이 아래로 줄이지 않는다(§7.3)
PLOT_DPI = 220                # plot 이미지 해상도(예전 150) — 확대해도 뭉개지지 않게
CAT_W_IN = 1.15               # CAT 계층 열 하나 (개수는 템플릿이 정한다)
ITEM_W_IN = 1.35
WAFER_W_IN = 0.62             # 9pt 숫자 한 칸이 들어가는 최소 폭
ROW_H_IN = 0.24
OVERFLOW_NOTE = "표가 슬라이드 밖으로 이어집니다 (편집 화면·인쇄에서는 온전)"

SPEC_FILL = RGBColor(0xFF, 0xEC, 0xEE)
SPEC_TXT = RGBColor(0xD7, 0x00, 0x15)
HDR_FILL = RGBColor(0xF2, 0xF2, 0xF4)     # 회색 헤더
ALT_FILL = RGBColor(0xFA, 0xFA, 0xFB)     # 교대 행
BODY_FILL = RGBColor(0xFF, 0xFF, 0xFF)
LINE_THIN = "E5E5EA"                      # 얇은 가로선
LINE_HEAD = "1D1D1F"                      # 헤더 아래만 진한 선
TEXT_COLOR = RGBColor(0x1D, 0x1D, 0x1F)


@dataclass
class TableData:
    """export.summary가 만들어 주는 표 하나 분량.

    `rows`의 `cats`는 CAT2 이후의 값들이고 **개수는 템플릿이 정한다**(§3.3) —
    CAT4·CAT5가 있으면 그만큼 열이 늘어난다. `cat_names`는 그 열 이름이다.
    """
    name: str                                  # CAT1
    header_lots: list[tuple[str, list[str]]]   # [(lot, [wafer,…]), …]
    rows: list[dict]                           # cats, item, values, offspec
    cat_names: list[str] = field(default_factory=list)

    def labels(self) -> list[str]:
        """라벨 열 이름 — CAT2…CATn + item."""
        n = max((len(r.get("cats") or ()) for r in self.rows), default=0)
        names = list(self.cat_names[:n])
        names += [f"CAT{i + 2}" for i in range(len(names), n)]
        return [*names, "item"]

    def label_values(self, row: dict) -> list[str]:
        """한 행의 라벨 칸 값 — 길이가 labels()와 항상 같다."""
        cats = list(row.get("cats") or ())
        cats += [""] * (len(self.labels()) - 1 - len(cats))
        return [*cats, row["item"]]


def table_mode_of(mode: str) -> str:
    """표 페이지 모드 정규화. 예전 설정 파일의 'wide'는 overflow와 같다(§10.8)."""
    return "split" if str(mode).lower() == "split" else "overflow"


def build_deck(
    report: ReportSpec,
    experiments: list[str],                    # factor 이름들. 반복 없으면 [""]
    group_styles_of,                           # exp → list[GroupStyle]
    plot_data_of,                              # (exp, PlotSpec) → {gid: DataFrame}
    tables: list[TableData],                   # CAT1마다 하나 — 실험과 무관하다
    rf: Reformatter,
    log_patterns: list[str],
    exclusion_log: pl.DataFrame,
    table_mode: str = "overflow",
    group_tables: list[TableData] | None = None,   # 그룹별 평균 표(표 뒤쪽)
    factors: pl.DataFrame | None = None,       # inline 계측 top-k (기능 B)
    meta: dict | None = None,                  # 표지에 넣을 메타데이터
    split_rows=None,                           # 실험 조건 매트릭스(wide)
    lot_split: bool = False,                   # lot마다 심볼을 달리할지(§9.2)
) -> Presentation:
    """페이지 순서: **표지 → (실험 조건) → plot 전부 → 표 전부 →
    (그룹별 평균 표) → (유의 인자) → 제외 이력**.

    표지·실험 조건·그룹별 평균은 사용자 요청으로 붙었고, 그 사이 순서는 확정
    사양(§7.2) 그대로다 — 표는 실험과 무관하므로 한 벌씩만 만든다.
    """
    prs = Presentation()
    prs.slide_width = Inches(BASE_W_IN)         # 항상 16:9 (§7.1)
    prs.slide_height = Inches(BASE_H_IN)
    blank = prs.slide_layouts[6]
    mode = table_mode_of(table_mode)

    if meta:
        _title_slide(prs, blank, meta)
    if split_rows is not None:
        _split_slide(prs, blank, split_rows)

    for exp in experiments:
        styles = group_styles_of(exp)
        for page in report.pages:
            slide = prs.slides.add_slide(blank)
            suffix = f" — {exp}" if exp else ""
            _add_title(slide, prs, page.title + suffix)
            _fill_slots(slide, prs, page, exp, styles,
                        plot_data_of, rf, log_patterns, lot_split)

    for td in tables:
        for part in (split_table(td) if mode == "split" else [td]):
            _table_slide(prs, blank, part)

    for td in (group_tables or []):            # 그룹별 평균 — 표 뒤쪽에 차례로
        for part in (split_table(td) if mode == "split" else [td]):
            _table_slide(prs, blank, part)

    if factors is not None and not factors.is_empty():
        _factor_slide(prs, blank, factors)
    _exclusion_slide(prs, blank, exclusion_log)
    return prs


# ── 내부 ─────────────────────────────────────────────────────
def _add_title(slide, prs, text: str, note: str = "") -> None:
    """제목 + (선택) 같은 줄 오른쪽에 붙는 작은 안내 문구."""
    tb = slide.shapes.add_textbox(
        Inches(MARGIN_IN), Inches(0.18),
        prs.slide_width - Inches(2 * MARGIN_IN), Inches(TITLE_H_IN))
    p = tb.text_frame.paragraphs[0]
    run = p.add_run()
    run.text = text
    run.font.size = Pt(20)
    run.font.bold = True
    if note:                       # 20pt로 붙이면 제목 줄이 넘친다
        n = p.add_run()
        n.text = f"    {note}"
        n.font.size = Pt(11)
        n.font.bold = False
        n.font.color.rgb = RGBColor(0x6E, 0x6E, 0x73)
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
                plot_data_of, rf, log_patterns,
                lot_split: bool = False) -> None:
    for i, spec in enumerate(page.slots):
        if spec is None:
            continue
        left, top, w, h = _slot_rect(prs, i)
        if spec.type == "table":
            # 표는 전용 페이지로 나간다(§7.3). 예전 템플릿의 Type=table 행은
            # 슬롯을 비우고 넘어간다 — 슬롯에도 넣으면 같은 표가 두 번 나온다.
            continue
        figsize = (w / 914400, h / 914400)        # EMU → inch
        fig = mpl_renderer.render(spec, plot_data_of(exp, spec),
                                  styles, rf, log_patterns, figsize,
                                  lot_split=lot_split)
        buf = io.BytesIO()
        try:
            fig.savefig(buf, format="png", dpi=PLOT_DPI)
        finally:
            # 덱 하나에 plot이 수백 개가 되므로 쓰고 나면 바로 버린다.
            fig.clear()
        buf.seek(0)
        pic = slide.shapes.add_picture(buf, left, top, width=w, height=h)
        pic.line.color.rgb = RGBColor(0xD2, 0xD2, 0xD7)
        pic.line.width = Pt(0.75)


def label_widths_in(n_labels: int) -> list[float]:
    """라벨 열 폭 — CAT 열들 + 마지막 item 열. CAT 개수는 템플릿이 정한다(§3.3)."""
    return [CAT_W_IN] * max(0, n_labels - 1) + [ITEM_W_IN]


def table_width_in(n_wafers: int, n_labels: int = 3) -> tuple[float, float, bool]:
    """(표 전체 폭, wafer 열 폭, 넘침 여부).

    9pt를 지키는 것이 우선이므로 열을 그 아래로 좁히지 않는다. 자리가 남으면
    고르게 늘려 슬라이드를 채우고, 모자라면 **그대로 넘치게 둔다**(§7.3).
    """
    usable = BASE_W_IN - 2 * MARGIN_IN
    label = sum(label_widths_in(n_labels))
    if n_wafers <= 0:
        return label, WAFER_W_IN, False
    fit = (usable - label) / n_wafers
    wafer_w = max(WAFER_W_IN, fit)
    total = label + n_wafers * wafer_w
    return total, wafer_w, total > usable + 1e-6


def _table_slide(prs, layout, td: TableData) -> None:
    """CAT1 하나 = 표 한 장 (§7.3). 스타일은 화면과 같은 톤(§7.4)."""
    slide = prs.slides.add_slide(layout)
    wafers = [(lot, wf) for lot, ws in td.header_lots for wf in ws]
    labels = td.labels()                      # CAT2…CATn + item (개수 자유)
    n_lab = len(labels)
    total_w, wafer_w, overflow = table_width_in(len(wafers), n_lab)
    _add_title(slide, prs, td.name, note=OVERFLOW_NOTE if overflow else "")

    n_rows, n_cols = 2 + len(td.rows), n_lab + len(wafers)
    top = Inches(0.18 + TITLE_H_IN + 0.2)
    height = Inches(min(BASE_H_IN - 1.5, n_rows * ROW_H_IN))
    tbl = slide.shapes.add_table(n_rows, n_cols, Inches(MARGIN_IN), top,
                                 Inches(total_w), height).table
    tbl.first_row = False          # 기본 파란 줄무늬 스타일 제거(§7.4)
    tbl.horz_banding = False
    for i, w in enumerate(label_widths_in(n_lab)):
        tbl.columns[i].width = Inches(w)
    for i in range(n_lab, n_cols):
        tbl.columns[i].width = Inches(wafer_w)
    for r in range(n_rows):
        tbl.rows[r].height = Inches(ROW_H_IN)

    # ── 병합 먼저 (§10.5) ────────────────────────────────────
    c = n_lab
    for _lot, ws in td.header_lots:
        if len(ws) > 1:
            tbl.cell(0, c).merge(tbl.cell(0, c + len(ws) - 1))
        c += len(ws)
    # 상위 CAT이 바뀌면 하위 병합도 끊는다(§3.3) — 키에 상위 값을 포함해서
    vals = [td.label_values(r) for r in td.rows]
    for col in range(n_lab - 1):              # item 열은 병합하지 않는다
        _merge_runs(tbl, col, [tuple(v[:col + 1]) for v in vals])

    # ── 값 나중 ──────────────────────────────────────────────
    for c, (lot, _wf) in enumerate(wafers, start=n_lab):
        _put(tbl.cell(0, c), lot, bold=True, align=PP_ALIGN.CENTER)
    for c, text in enumerate(labels):
        _put(tbl.cell(1, c), text, bold=True)
    for c, (_lot, wf) in enumerate(wafers, start=n_lab):
        _put(tbl.cell(1, c), wf, bold=True, align=PP_ALIGN.CENTER)

    for i, row in enumerate(td.rows):
        r = i + 2
        fill = ALT_FILL if i % 2 else BODY_FILL
        for c, text in enumerate(vals[i]):
            _put(tbl.cell(r, c), text, fill=fill,
                 anchor=MSO_ANCHOR.MIDDLE if c < n_lab - 1 else MSO_ANCHOR.TOP)
        for c, (v, off) in enumerate(zip(row["values"], row["offspec"]),
                                     start=n_lab):
            text = (fmt_value(v) if v is None or isinstance(v, (int, float))
                    else str(v))          # 실험 조건 표는 값이 코드 문자열이다
            _put(tbl.cell(r, c), text,
                 align=PP_ALIGN.RIGHT if not isinstance(v, str)
                 else PP_ALIGN.CENTER,
                 fill=SPEC_FILL if off else fill,       # 규격 이탈 셀만 붉게
                 color=SPEC_TXT if off else TEXT_COLOR, bold=bool(off))

    # ── 선: 행마다 얇게, 헤더 아래만 진하게 ──────────────────
    for c in range(n_cols):
        _bottom_line(tbl.cell(1, c), 1.25, LINE_HEAD)
        for r in range(2, n_rows):
            _bottom_line(tbl.cell(r, c), 0.5, LINE_THIN)


def _merge_runs(tbl, col: int, keys: list[tuple]) -> None:
    """같은 값이 연속되는 구간을 세로 병합. 본문은 2행부터 시작한다.

    keys에는 상위 CAT까지 담은 튜플을 넘긴다 — 상위가 바뀌면 값이 같아도 다른
    그룹이므로 병합을 끊어야 한다(§3.3 확정).
    """
    start = 0
    for i in range(1, len(keys) + 1):
        if i == len(keys) or keys[i] != keys[start]:
            if i - start > 1:
                tbl.cell(start + 2, col).merge(tbl.cell(i + 1, col))
            start = i


def _put(cell, text: str, *, bold: bool = False, align=PP_ALIGN.LEFT,
         fill: RGBColor | None = HDR_FILL, color: RGBColor = TEXT_COLOR,
         anchor=MSO_ANCHOR.TOP) -> None:
    """셀 하나에 값·서식. 병합에 먹힌 칸은 건드리지 않는다."""
    if cell.is_spanned:
        return
    cell.text = text
    cell.vertical_anchor = anchor
    cell.margin_left = cell.margin_right = Inches(0.04)
    cell.margin_top = cell.margin_bottom = Inches(0.01)
    if fill is not None:
        cell.fill.solid()
        cell.fill.fore_color.rgb = fill
    for p in cell.text_frame.paragraphs:
        p.alignment = align
        # 문단 기본값과 run 양쪽에 건다 — run에 크기가 비어 있으면 뷰어가
        # 테마 기본(18pt)으로 그려서 9pt 규칙이 조용히 깨진다.
        for f in (p.font, *(r.font for r in p.runs)):
            f.size = FONT_MIN           # 9pt — 이 아래로 내리지 않는다
            f.bold = bold
            f.color.rgb = color


def _bottom_line(cell, width_pt: float, color: str) -> None:
    """셀 아래 가로선. python-pptx가 테두리 API를 주지 않아 XML로 넣는다.

    `a:lnB`는 tcPr 안에서 채우기(fill)보다 **앞**에 와야 한다. 우리는 lnL/lnR/
    lnT를 쓰지 않으므로 맨 앞에 끼우면 순서가 항상 맞는다(순서가 어긋나면
    PowerPoint가 '복구' 창을 띄운다).
    """
    if cell.is_spanned:
        return
    tc_pr = cell._tc.get_or_add_tcPr()
    for old in tc_pr.findall(qn("a:lnB")):
        tc_pr.remove(old)
    tc_pr.insert(0, parse_xml(
        f'<a:lnB {nsdecls("a")} w="{int(width_pt * 12700)}" cap="flat" '
        f'cmpd="sng" algn="ctr"><a:solidFill><a:srgbClr val="{color}"/>'
        f'</a:solidFill><a:prstDash val="solid"/></a:lnB>'))


def split_table(td: TableData, per: int = WAFERS_PER_SLIDE) -> list[TableData]:
    """split 모드: **lot 경계로 먼저** 끊고, 한 lot이 per장을 넘으면 그 안에서
    다시 끊는다 (제목에 1/N).

    lot 경계를 무시하고 12장씩 자르면 한 장에 두 lot이 걸치고 lot 머리글이
    슬라이드 경계에서 잘린다(§9.2). lot이 하나면 예전과 결과가 같다 —
    25장이면 12/12/1 그대로다. lot이 둘이면 각각 5장이어도 두 장으로 나뉜다.
    """
    groups = [(lot, list(ws)) for lot, ws in td.header_lots if ws]
    if not groups:
        return [td]

    # 조각마다 원래 열 위치를 들고 다닌다 — 같은 (lot, wafer)가 두 번 나와도
    # 위치로 자르면 값이 어긋나지 않는다.
    segments: list[list[int]] = []
    pos = 0
    for _lot, ws in groups:
        idx = list(range(pos, pos + len(ws)))
        pos += len(ws)
        for i in range(0, len(idx), per):
            segments.append(idx[i:i + per])
    if len(segments) <= 1:
        return [td]

    flat = [(lot, wf) for lot, ws in groups for wf in ws]
    parts: list[TableData] = []
    n = len(segments)
    for k, idx in enumerate(segments):
        seg = [flat[i] for i in idx]
        lots: list[tuple[str, list[str]]] = []
        for lot, wf in seg:
            if lots and lots[-1][0] == lot:
                lots[-1][1].append(wf)
            else:
                lots.append((lot, [wf]))
        rows = [{**r,
                 "values": [r["values"][i] for i in idx],
                 "offspec": [r["offspec"][i] for i in idx]} for r in td.rows]
        parts.append(TableData(f"{td.name}  ({k + 1}/{n})", lots, rows,
                               cat_names=list(td.cat_names)))
    return parts


def _title_slide(prs, layout, meta: dict) -> None:
    """표지 — 무엇을 언제 뽑은 리포트인지 한눈에.

    LINE_ID·ROOT_LOT_ID·STEP_ID·TEMPERATURE·TKOUT_TIME(마지막 측정 기준)을
    담는다. 값이 없는 항목은 줄째로 뺀다 — 빈 칸이 늘어서는 것보다 낫다.
    """
    slide = prs.slides.add_slide(layout)
    tb = slide.shapes.add_textbox(Inches(1.0), Inches(2.1),
                                  prs.slide_width - Inches(2.0), Inches(1.2))
    p = tb.text_frame.paragraphs[0]
    run = p.add_run()
    run.text = str(meta.get("title") or "ET Report")
    run.font.size = Pt(40)
    run.font.bold = True

    body = slide.shapes.add_textbox(Inches(1.0), Inches(3.5),
                                    prs.slide_width - Inches(2.0), Inches(2.6))
    tf = body.text_frame
    tf.word_wrap = True
    first = True
    for label in ("LINE_ID", "ROOT_LOT_ID", "STEP_ID", "TEMPERATURE",
                  "TKOUT_TIME"):
        val = meta.get(label)
        if not val:
            continue
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        para.text = f"{label}      {val}"
        para.font.size = Pt(15)
        para.font.color.rgb = TEXT_COLOR


def _split_slide(prs, layout, wide) -> None:
    """실험 조건 한 장 — 어떤 wafer가 어떤 조건이었는지 정리한 표.

    표 렌더러(§7.4 스타일)를 그대로 쓰되 값이 숫자가 아니라 조건 코드다.
    """
    if wide is None or wide.is_empty():
        return
    steps = [c for c in wide.columns if c not in ("lot", "wafer")]
    rows = [{"cats": [rec["lot"]], "item": rec["wafer"],
             "values": [rec[s] for s in steps],
             "offspec": [False] * len(steps)}
            for rec in wide.iter_rows(named=True)]
    _table_slide(prs, layout,
                 TableData("실험 조건", [("step 조건", steps)], rows,
                           cat_names=["lot"]))


def _factor_slide(prs, layout, top: pl.DataFrame) -> None:
    """inline 계측 유의 인자 top-k — 표 한 장(§7.4 스타일 공용).

    분석 대상 lot 안에서 계측 인자와 ET item의 상관(r)·그룹 간 유의차(t)를
    훑은 결과다. 숫자는 표 렌더러가 아니라 여기서 직접 쓰되 자릿수 규칙
    (`fmt_value`)은 같은 것을 쓴다.
    """
    rows, suspect = [], 0
    for rec in top.iter_rows(named=True):
        r, rw = rec.get("r"), rec.get("r_within")
        if _lot_effect(r, rw):
            suspect += 1
        rows.append({
            "cats": [rec["met"]],
            "item": rec["item"],
            "values": [r, rw, rec.get("t"), float(rec.get("n") or 0)],
            "offspec": [False, False, False, False]})
    name = "유의 인자 top-k (inline 계측)"
    if suspect:
        name += f"  — lot 효과 의심 {suspect}건"
    td = TableData(name,
                   [("통계", ["상관 r", "lot 내 r", "그룹차 t", "n"])], rows,
                   cat_names=["계측 인자"])
    _table_slide(prs, layout, td)


def _lot_effect(r, r_within) -> bool:
    """합친 상관과 lot 내 상관이 크게 갈리면 lot 효과를 의심한다.

    부호가 뒤집히거나 크기가 두 배 넘게 차이 나면, 그 상관은 계측값과 ET값의
    관계가 아니라 **lot 사이의 평균 차이**를 보고 있을 가능성이 크다.
    """
    if r is None or r_within is None:
        return False
    if (r > 0) != (r_within > 0) and abs(r) > 0.2 and abs(r_within) > 0.2:
        return True
    return abs(r) > 2 * abs(r_within) + 0.2


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
