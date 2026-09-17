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
LEGEND_H_IN = 0.40        # 페이지 공통 범례 한 줄(§8)
SPEC_LABELS = ["규격 하한", "규격 상한"]   # 표의 규격 열 이름(§14)
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

    def cat_labels(self) -> list[str]:
        """CAT2…CATn 열 이름 — 개수는 템플릿이 정한다(§3.3)."""
        n = max((len(r.get("cats") or ()) for r in self.rows), default=0)
        names = list(self.cat_names[:n])
        names += [f"CAT{i + 2}" for i in range(len(names), n)]
        return names

    def spec_labels(self) -> list[str]:
        """규격 열(§14). 자리는 **item 뒤·wafer 앞** — 값을 읽기 전에 기준을
        먼저 본다. 리포메터에 규격이 하나도 없는 표에는 붙이지 않는다(빈 열
        두 개가 wafer를 밀어낼 이유가 없다)."""
        return list(SPEC_LABELS) if any(
            any(r.get("spec") or ()) for r in self.rows) else []

    def labels(self) -> list[str]:
        """라벨 열 이름 — CAT2…CATn + item + 규격."""
        return [*self.cat_labels(), "item", *self.spec_labels()]

    def label_values(self, row: dict) -> list[str]:
        """한 행의 라벨 칸 값 — 길이가 labels()와 항상 같다."""
        cats = list(row.get("cats") or ())
        cats += [""] * (len(self.cat_labels()) - len(cats))
        n = len(self.spec_labels())
        spec = list(row.get("spec") or ())[:n]
        spec += [""] * (n - len(spec))
        return [*cats, row["item"], *spec]


def table_mode_of(mode: str) -> str:
    """표 페이지 모드 정규화. 예전 설정 파일의 'wide'는 overflow와 같다(§10.8)."""
    return "split" if str(mode).lower() == "split" else "overflow"


def set_grid(frame, col_widths: list[int], row_h: int) -> None:
    """표의 열 폭·행 높이를 한 번에 — **python-pptx의 setter를 쓰지 않는다.**

    `table.columns[i].width = …`는 setter마다 `notify_width_changed()`를 부르고,
    그 안에서 `sum(col.width for col in self.columns)`가 전체 열을 다시 훑는다.
    `columns[idx]`는 그때마다 `gridCol_lst`(lxml findall)를 새로 만들기 때문에,
    열 N개를 정하는 데 XML 스캔이 N²번 일어난다. 행 높이도 같은 구조다.

    wafer가 많아질수록 이 항이 표 한 장의 시간을 지배한다 — 측정으로 표 1장이
    wafer 43장에서 3.6초, 344장에서 57.8초였고 그중 대부분이 여기였다. 아래처럼
    XML에 직접 쓰면 43장 2.5초·344장 25.0초가 된다(덱 전체로는 6배).

    setter를 건너뛰므로 그래픽 프레임 크기가 자동으로 따라오지 않는다. 그래서
    **합을 여기서 직접 대입한다** — python-pptx가 계산하던 값(열 폭의 합, 행
    높이의 합)과 같은 값이라 산출물은 동일하다.
    """
    tbl = frame.table._tbl
    for gridCol, w in zip(tbl.tblGrid.gridCol_lst, col_widths):
        gridCol.w = Emu(w)
    rows = tbl.tr_lst
    for tr in rows:
        tr.h = Emu(row_h)
    frame.width = Emu(sum(col_widths))
    frame.height = Emu(row_h * len(rows))


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
    track_rows=None,                           # fab tracking에서 뽑은 컬럼(기능 A)
    lot_split: bool = False,                   # lot마다 심볼을 달리할지(§9.2)
    on_progress=None,                          # (done, total, 라벨) — 진행 표시용
) -> Presentation:
    """페이지 순서: **표지 → (실험 조건) → (fab tracking) → plot 전부 → 표 전부 →
    (그룹별 평균 표) → (유의 인자) → 제외 이력**.

    표지·실험 조건·그룹별 평균은 사용자 요청으로 붙었고, 그 사이 순서는 확정
    사양(§7.2) 그대로다 — 표는 실험과 무관하므로 한 벌씩만 만든다.

    `on_progress(done, total, 라벨)`을 주면 슬라이드 한 장을 마칠 때마다 부른다.
    덱은 수십 초~수 분이 걸리는데 진행이 보이지 않으면 멈춘 것과 구별되지 않는다.
    분할(split)은 **미리 한 번만** 계산해 총 장수를 먼저 확정한다 — 진행률의
    분모가 도중에 바뀌면 막대가 뒤로 가는 것처럼 보인다.
    """
    prs = Presentation()
    prs.slide_width = Inches(BASE_W_IN)         # 항상 16:9 (§7.1)
    prs.slide_height = Inches(BASE_H_IN)
    blank = prs.slide_layouts[6]
    mode = table_mode_of(table_mode)

    def parts_of(tds: list[TableData]) -> list[TableData]:
        return [p for td in tds
                for p in (split_table(td) if mode == "split" else [td])]

    table_parts = parts_of(tables)
    group_parts = parts_of(group_tables or [])
    n_plot = len(experiments) * len(report.pages)
    has_track = track_rows is not None and not track_rows.is_empty()
    total = (bool(meta) + (split_rows is not None) + has_track + n_plot
             + len(table_parts) + len(group_parts)
             + (factors is not None and not factors.is_empty()) + 1)
    done = 0

    def tick(label: str) -> None:
        nonlocal done
        done += 1
        if on_progress:
            on_progress(done, total, label)

    if meta:
        _title_slide(prs, blank, meta)
        tick("표지")
    if split_rows is not None:
        _split_slide(prs, blank, split_rows)
        tick("실험 조건")
    if has_track:
        # fab tracking에서 뽑아 붙인 컬럼 — **이름은 사용자가 정한 것 그대로**
        # 머리글이 된다(그게 이 기능의 요점이다).
        _split_slide(prs, blank, track_rows, name="fab tracking",
                     group_label="공정 조건")
        tick("fab tracking")

    for exp in experiments:
        styles = group_styles_of(exp)
        for page in report.pages:
            slide = prs.slides.add_slide(blank)
            suffix = f" — {exp}" if exp else ""
            _add_title(slide, prs, page.title + suffix)
            _fill_slots(slide, prs, page, exp, styles,
                        plot_data_of, rf, log_patterns, lot_split)
            tick(f"plot — {page.title}{suffix}")

    for part in table_parts:
        _table_slide(prs, blank, part)
        tick(f"표 — {part.name}")

    for part in group_parts:                   # 그룹별 평균 — 표 뒤쪽에 차례로
        _table_slide(prs, blank, part)
        tick(f"표 — {part.name}")

    if factors is not None and not factors.is_empty():
        _factor_slide(prs, blank, factors)
        tick("유의 인자")
    _exclusion_slide(prs, blank, exclusion_log)
    tick("제외 이력")
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


def _slot_rect(prs, idx: int, legend_h: int = 0):
    """order-1 (0~5) → (left, top, w, h). 1·2·3 윗줄 / 4·5·6 아랫줄, 왼→오.

    **그림은 정사각형**이다(§8). 슬롯 칸은 가로로 길어서 그 안을 다 채우면
    x축만 늘어난 그림이 되고, 같은 데이터가 페이지마다 다른 비율로 보인다.
    칸 안에서 짧은 변에 맞춘 정사각형을 **가운데**에 둔다. `legend_h`만큼은
    페이지 공통 범례 자리로 아래에서 덜어 낸다.
    """
    top0 = Inches(0.18 + TITLE_H_IN + 0.12)
    W = prs.slide_width - Inches(2 * MARGIN_IN)
    H = prs.slide_height - top0 - Inches(MARGIN_IN) - legend_h
    gw, gh = W / 3, H / 2
    r, c = divmod(idx, 3)
    pad = Emu(45720)
    side = min(gw, gh) - 2 * pad
    return (int(Inches(MARGIN_IN) + c * gw + (gw - side) / 2),
            int(top0 + r * gh + (gh - side) / 2), int(side), int(side))


def _legend_sample(handle) -> tuple:
    """범례 표본의 (색, 마커, 크기). Line2D도 scatter(PathCollection)도 받는다.

    렌더러는 그룹 안에서 색·크기가 균일하면 Line2D로 그린다(비용 규약) —
    두 종류가 한 페이지에 섞일 수 있어 둘 다 읽는다. PathCollection에서는
    마커 모양을 되돌릴 길이 없어 동그라미로 적는다.
    """
    if hasattr(handle, "get_marker"):
        return (handle.get_color(), handle.get_marker(),
                handle.get_markersize(), handle.get_markerfacecolor())
    fc = handle.get_facecolor()
    c = tuple(fc[0]) if len(fc) else "#000000"
    return (c, "o", 6.0, c)


def _legend_strip(slide, prs, entries: dict, top: int, height: int) -> None:
    """페이지 공통 범례 한 줄(§8) — 슬라이드 아래에 가로로 눕힌다.

    plot마다 범례를 달면 같은 그룹 이름이 여섯 번 반복되고, 그 자리만큼
    그림이 좁아져 정사각형이 되지 않는다. 한 페이지의 그림들은 같은 그룹
    스타일을 공유하므로(확정 사양) 범례도 한 벌이면 된다.
    """
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D

    left = Inches(MARGIN_IN)
    width = prs.slide_width - Inches(2 * MARGIN_IN)
    fig = Figure(figsize=(width / 914400, height / 914400), dpi=PLOT_DPI)
    fig.patch.set_facecolor("white")
    handles = [Line2D([], [], color=c, marker=m, markersize=sz,
                      markerfacecolor=fc, linestyle="none")
               for c, m, sz, fc in entries.values()]
    fig.legend(handles, list(entries), loc="center", frameon=False,
               ncol=min(len(entries), 8), fontsize=8, handletextpad=0.4,
               columnspacing=1.4)
    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", dpi=PLOT_DPI)
    finally:
        fig.clear()
    buf.seek(0)
    slide.shapes.add_picture(buf, left, top, width=width, height=height)


def _fill_slots(slide, prs, page: PageSpec, exp, styles,
                plot_data_of, rf, log_patterns,
                lot_split: bool = False) -> None:
    legend_h = Inches(LEGEND_H_IN)
    entries: dict[str, tuple] = {}
    for i, spec in enumerate(page.slots):
        if spec is None:
            continue
        left, top, w, h = _slot_rect(prs, i, legend_h)
        if spec.type == "table":
            # 표는 전용 페이지로 나간다(§7.3). 예전 템플릿의 Type=table 행은
            # 슬롯을 비우고 넘어간다 — 슬롯에도 넣으면 같은 표가 두 번 나온다.
            continue
        figsize = (w / 914400, h / 914400)        # EMU → inch
        fig = mpl_renderer.render(spec, plot_data_of(exp, spec),
                                  styles, rf, log_patterns, figsize,
                                  lot_split=lot_split, legend=False)
        buf = io.BytesIO()
        try:
            for ax in fig.axes:                 # 범례는 페이지에 한 벌만(§8)
                for hd, lab in zip(*ax.get_legend_handles_labels()):
                    entries.setdefault(lab, _legend_sample(hd))
            fig.savefig(buf, format="png", dpi=PLOT_DPI)
        finally:
            # 덱 하나에 plot이 수백 개가 되므로 쓰고 나면 바로 버린다.
            fig.clear()
        buf.seek(0)
        pic = slide.shapes.add_picture(buf, left, top, width=w, height=h)
        pic.line.color.rgb = RGBColor(0xD2, 0xD2, 0xD7)
        pic.line.width = Pt(0.75)
    if entries:
        _legend_strip(slide, prs, entries,
                      prs.slide_height - Inches(MARGIN_IN) - legend_h, legend_h)


def label_widths_in(n_labels: int, n_spec: int = 0) -> list[float]:
    """라벨 열 폭 — CAT 열들 + item 열 + 규격 열들(§14).

    규격은 숫자 두 칸이라 CAT 폭이면 넉넉하다. CAT 개수는 템플릿이 정한다(§3.3).
    """
    return ([CAT_W_IN] * max(0, n_labels - 1 - n_spec) + [ITEM_W_IN]
            + [CAT_W_IN] * n_spec)


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
    labels = td.labels()                      # CAT2…CATn + item + 규격(§14)
    n_lab, n_spec = len(labels), len(td.spec_labels())
    total_w, wafer_w, overflow = table_width_in(len(wafers), n_lab)
    widths = [Inches(w) for w in label_widths_in(n_lab, n_spec)]
    _add_title(slide, prs, td.name, note=OVERFLOW_NOTE if overflow else "")

    n_rows, n_cols = 2 + len(td.rows), n_lab + len(wafers)
    top = Inches(0.18 + TITLE_H_IN + 0.2)
    height = Inches(min(BASE_H_IN - 1.5, n_rows * ROW_H_IN))
    frame = slide.shapes.add_table(n_rows, n_cols, Inches(MARGIN_IN), top,
                                   Inches(total_w), height)
    tbl = frame.table
    tbl.first_row = False          # 기본 파란 줄무늬 스타일 제거(§7.4)
    tbl.horz_banding = False
    # 열 폭·행 높이는 set_grid로 — python-pptx의 setter는 열 수의 제곱으로 느리다
    widths += [Inches(wafer_w)] * (n_cols - n_lab)
    set_grid(frame, widths, Inches(ROW_H_IN))

    # ── 병합 먼저 (§10.5) ────────────────────────────────────
    c = n_lab
    for _lot, ws in td.header_lots:
        if len(ws) > 1:
            tbl.cell(0, c).merge(tbl.cell(0, c + len(ws) - 1))
        c += len(ws)
    # 상위 CAT이 바뀌면 하위 병합도 끊는다(§3.3) — 키에 상위 값을 포함해서
    vals = [td.label_values(r) for r in td.rows]
    for col in range(n_lab - 1 - n_spec):     # item·규격 열은 병합하지 않는다
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
                 anchor=MSO_ANCHOR.MIDDLE if c < n_lab - 1 - n_spec
                 else MSO_ANCHOR.TOP)
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


def _split_slide(prs, layout, wide, name: str = "실험 조건",
                 group_label: str = "step 조건") -> None:
    """`lot | wafer | <열들…>` 한 장 — 어떤 wafer가 어떤 조건이었는지 정리한 표.

    표 렌더러(§7.4 스타일)를 그대로 쓰되 값이 숫자가 아니라 조건 코드다.
    실험 조건(split)과 fab tracking에서 뽑은 컬럼이 **모양이 같아** 같은 함수를
    쓴다 — 제목과 열 묶음 이름만 다르다.
    """
    if wide is None or wide.is_empty():
        return
    steps = [c for c in wide.columns if c not in ("lot", "wafer")]
    if not steps:
        return
    rows = [{"cats": [rec["lot"]], "item": rec["wafer"],
             "values": [rec[s] for s in steps],
             "offspec": [False] * len(steps)}
            for rec in wide.iter_rows(named=True)]
    _table_slide(prs, layout,
                 TableData(name, [(group_label, steps)], rows,
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
