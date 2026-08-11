"""Summary 집계와 xlsx 내보내기.

- CAT1 하나 = 표 하나 = 시트 하나
- 병합(lot 가로 / CAT2 세로), 규격 이탈 셀 붉은 배경, 틀 고정, 자릿수 규칙
- Excel 처리는 xlwings만 사용(제약). 배포 PC에 Excel 존재 확인됨.
- 클립보드 복사는 TSV 문자열을 만들어 Qt 클립보드로 (UI 쪽에서 호출)
"""
from __future__ import annotations

from dataclasses import dataclass

from etreport.model.specs import fmt_value
from etreport.render.pptgen import TableData


@dataclass
class SummaryOptions:
    agg: str = "avg"            # avg | std   (동시 표시는 필요 없음 — 확정)
    delta_vs_ref: bool = False
    session_caption: str = ""


# ── 집계 ─────────────────────────────────────────────────────
def build_table(state, cat1: str, opt: SummaryOptions) -> TableData:
    """AppState의 wide 데이터로 CAT1 표 하나를 만든다.

    DB를 다시 조회하지 않는다 — 분석은 읽기 전용이고, 화면·xlsx·PPT가
    같은 숫자를 보여야 하므로 집계 소스를 하나로 둔다(model/aggregate.py).
    """
    from etreport.model.aggregate import offspec, ref_values, wafer_stats

    header = state.wafer_columns()
    rows_spec = [r for r in state.report.table_rows if r.cat1 == cat1]
    aliases = [r.item_id for r in rows_spec]
    ws = wafer_stats(state.data, state.excluded, aliases, opt.agg)
    ref = {}
    if opt.delta_vs_ref:
        g = state.ref_group()
        ref = ref_values(state.data, state.excluded, g.gid if g else None,
                         aliases, opt.agg)

    rows = []
    for rs in rows_spec:
        rule = state.rf.by_alias.get(rs.item_id)
        rv = ref.get(rs.item_id)
        vals, offs = [], []
        for lot, wl in header:
            for wf in wl:
                v = ws.get(rs.item_id, lot, wf)
                off = (opt.agg == "avg" and not opt.delta_vs_ref
                       and offspec(v, rule))
                if opt.delta_vs_ref and v is not None and rv is not None:
                    v -= rv
                vals.append(v)
                offs.append(off)
        rows.append({"cat2": rs.cat2, "cat3": rs.cat3, "item": rs.item_id,
                     "values": vals, "offspec": offs})
    return TableData(cat1, header, rows)


def to_tsv(td: TableData) -> str:
    lines = ["\t".join(["", "", ""] + [lot for lot, ws in td.header_lots
                                       for _ in ws]),
             "\t".join(["CAT2", "CAT3", "item"] +
                       [wf for _, ws in td.header_lots for wf in ws])]
    for r in td.rows:
        lines.append("\t".join([r["cat2"], r["cat3"], r["item"]] +
                               [fmt_value(v) for v in r["values"]]))
    return "\n".join(lines)


# ── xlsx (xlwings) ───────────────────────────────────────────
def export_xlsx(tables: list[TableData], path: str, opt: SummaryOptions) -> None:
    import xlwings as xw
    from xlwings.constants import HAlign, VAlign

    RED_BG, RED_TX, HDR_BG, CAT_BG = 0xEEECFF, 0x1500D7, 0xF5F3F2, 0xFBFAFA
    with xw.App(visible=False, add_book=False) as app:
        wb = app.books.add()
        for td in tables:
            sht = wb.sheets.add(td.name[:31], after=wb.sheets[-1])
            n_w = sum(len(ws) for _, ws in td.header_lots)
            # 제목
            sht["A1"].value = td.name
            sht["A1"].font.size, sht["A1"].font.bold = 14, True
            # 헤더 2행
            sht["A2"].value = [["CAT2", "CAT3", "item"] +
                               [lot for lot, ws in td.header_lots for _ in ws],
                               ["", "", ""] +
                               [wf for _, ws in td.header_lots for wf in ws]]
            c = 4
            for _lot, ws in td.header_lots:             # lot 가로 병합
                if len(ws) > 1:
                    sht.range((2, c), (2, c + len(ws) - 1)).merge()
                c += len(ws)
            for col in range(1, 4):                     # 라벨 세로 병합
                sht.range((2, col), (3, col)).merge()
            hdr = sht.range((2, 1), (3, 3 + n_w))
            hdr.color = HDR_BG
            hdr.font.bold = True
            hdr.api.HorizontalAlignment = HAlign.xlHAlignCenter
            # 본문
            body = [[r["cat2"], r["cat3"], r["item"]] +
                    [None if v is None else round(v, 6) for v in r["values"]]
                    for r in td.rows]
            sht["A4"].value = body
            for ri, r in enumerate(td.rows, start=4):   # 자릿수 + 규격 이탈
                for ci, (v, off) in enumerate(zip(r["values"], r["offspec"]),
                                              start=4):
                    cell = sht.range((ri, ci))
                    if v is not None:
                        a = abs(v)
                        cell.number_format = ("0.000" if a < 1
                                              else "0.00" if a <= 10 else "0.0")
                    if off:
                        cell.color = RED_BG
                        cell.font.color = RED_TX
                        cell.font.bold = True
            # CAT2 세로 병합
            r0, prev = 4, td.rows[0]["cat2"] if td.rows else None
            for r in range(5, 4 + len(td.rows) + 1):
                cur = td.rows[r - 4]["cat2"] if r - 4 < len(td.rows) else None
                if cur != prev:
                    if r - 1 > r0:
                        rng = sht.range((r0, 1), (r - 1, 1))
                        rng.merge()
                        rng.color = CAT_BG
                        rng.api.VerticalAlignment = VAlign.xlVAlignCenter
                    r0, prev = r, cur
            # 테두리·너비·틀 고정·캡션
            full = sht.range((2, 1), (3 + len(td.rows), 3 + n_w))
            for b in range(7, 13):
                full.api.Borders(b).Weight = 2
            sht.range((1, 1), (1, 3)).column_width = (10, 20, 7)
            sht.range((1, 4), (1, 3 + n_w)).column_width = 9
            sht.range((4 + len(td.rows) + 1, 1)).value = opt.session_caption
            sht.api.Application.ActiveWindow.SplitRow = 3
            sht.api.Application.ActiveWindow.SplitColumn = 3
            sht.api.Application.ActiveWindow.FreezePanes = True
        if len(wb.sheets) > len(tables):
            wb.sheets[0].delete()                        # 기본 빈 시트 제거
        wb.save(path)
        wb.close()
