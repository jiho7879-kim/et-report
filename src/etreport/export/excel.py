"""요약 집계와 xlsx 내보내기.

- CAT1 하나 = 표 하나 = 시트 하나
- 병합(lot 가로 / CAT2 세로), 규격 이탈 셀 붉은 배경, 틀 고정, 자릿수 규칙
- Excel 처리는 xlwings만 사용(제약). 배포 PC에 Excel 존재 확인됨.
- 클립보드 복사는 TSV 문자열을 만들어 Qt 클립보드로 (UI 쪽에서 호출)
"""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from etreport.model.specs import fmt_value
from etreport.render.pptgen import TableData


@dataclass
class SummaryOptions:
    #: avg(wafer 평균) | std(wafer 내 산포) | gavg(그룹별 평균)
    #: | gwafer(그룹으로 묶은 wafer 표)
    #: gavg는 열이 그룹 하나씩, gwafer는 열이 wafer이되 그룹 머리글로 묶인다.
    agg: str = "avg"
    delta_vs_ref: bool = False
    session_caption: str = ""


def spec_cells(rule) -> list[str]:
    """규격 하한·상한 셀(§14). 없는 쪽은 빈칸 — 한쪽만 있는 item이 흔하다."""
    if rule is None:
        return ["", ""]
    return [fmt_value(v) if v is not None else ""
            for v in (rule.speclow, rule.spechigh)]


# ── 집계 ─────────────────────────────────────────────────────
def build_table(state, cat1: str, opt: SummaryOptions) -> TableData:
    """AppState의 wide 데이터로 CAT1 표 하나를 만든다.

    DB를 다시 조회하지 않는다 — 분석은 읽기 전용이고, 화면·xlsx·PPT가
    같은 숫자를 보여야 하므로 집계 소스를 하나로 둔다(model/aggregate.py).
    """
    from etreport.model.aggregate import offspec, ref_values, wafer_stats

    if opt.agg == "gavg":
        return _group_table(state, cat1, opt)
    if opt.agg == "gwafer":
        return _group_wafer_table(state, cat1, opt)
    header = state.wafer_columns()
    rows_spec = [r for r in state.report.table_rows if r.cat1 == cat1]
    aliases = [r.item_id for r in rows_spec]
    ws = wafer_stats(state.data, state.hidden(), aliases, opt.agg)
    ref = {}
    if opt.delta_vs_ref:
        g = state.ref_group()
        ref = ref_values(state.data, state.hidden(), g.gid if g else None,
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
        rows.append({"cats": rs.subcats, "item": rs.item_id,
                     "spec": spec_cells(rule), "values": vals, "offspec": offs})
    return TableData(cat1, header, rows,
                     cat_names=list(getattr(state.report, "cat_names", []) or []))


def group_wafer_columns(state) -> list[tuple[str, list[tuple[str, str]]]]:
    """그룹 머리글 → 그 그룹의 (lot, wafer) 목록. **그룹 순서로 정렬**한다.

    표의 lot 머리글 자리에 그룹 이름이 오고, 그 아래에 소속 wafer가 늘어선다.
    미배정 wafer는 그룹이 하나라도 있으면 빼고(plot과 같은 기준), 그룹이 아예
    없으면 lot 머리글로 되돌아간다.
    """
    df = state.data
    if df is None or df.is_empty():
        return []
    import re

    def wkey(w: str) -> list:
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", w)]

    groups = [g for g in state.groups if g.visible]
    if not groups or "gid" not in df.columns:
        return [(lot, [(lot, w) for w in ws]) for lot, ws in
                state.wafer_columns()]
    out: list[tuple[str, list[tuple[str, str]]]] = []
    for g in groups:
        sub = df.filter(pl.col("gid") == g.gid)
        pairs = sorted({(lot, w) for lot, w in zip(sub["lot"], sub["wafer"])},
                       key=lambda p: (p[0], wkey(p[1])))
        if pairs:
            out.append((g.name, pairs))
    return out


def _group_wafer_table(state, cat1: str, opt: SummaryOptions) -> TableData:
    """그룹으로 묶은 wafer 표 — 값은 wafer 평균(또는 산포)."""
    from etreport.model.aggregate import offspec, ref_values, wafer_stats

    rows_spec = [r for r in state.report.table_rows if r.cat1 == cat1]
    aliases = [r.item_id for r in rows_spec]
    header = group_wafer_columns(state)
    ws = wafer_stats(state.data, state.hidden(), aliases, "avg")
    ref = {}
    if opt.delta_vs_ref:
        g = state.ref_group()
        ref = ref_values(state.data, state.hidden(), g.gid if g else None,
                         aliases, "avg")

    rows = []
    for rs in rows_spec:
        rule = state.rf.by_alias.get(rs.item_id)
        rv = ref.get(rs.item_id)
        vals, offs = [], []
        for _name, pairs in header:
            for lot, wf in pairs:
                v = ws.get(rs.item_id, lot, wf)
                off = not opt.delta_vs_ref and offspec(v, rule)
                if opt.delta_vs_ref and v is not None and rv is not None:
                    v -= rv
                vals.append(v)
                offs.append(off)
        rows.append({"cats": rs.subcats, "item": rs.item_id,
                     "spec": spec_cells(rule), "values": vals, "offspec": offs})
    # 그룹 기준 헤더면 wafer 셀에 "lot·wafer"를 남긴다(그룹 이름 ≠ lot)
    grouped = any(name != lot for name, pairs in header for lot, _ in pairs)
    header_lots = [(name, [f"{lot}·{w}" if grouped else w for lot, w in pairs])
                   for name, pairs in header]
    return TableData(cat1, header_lots, rows,
                     cat_names=list(getattr(state.report, "cat_names", []) or []))


def _group_table(state, cat1: str, opt: SummaryOptions) -> TableData:
    """그룹별 평균 표 — 열이 wafer 대신 **그룹**이다.

    각 그룹의 (lot, wafer) 평균을 다시 평균한다. wafer 집계는
    `model/aggregate.wafer_stats`를 그대로 쓰므로 화면·xlsx·PPT가 같은 숫자다.
    """
    from etreport.model.aggregate import offspec, wafer_stats

    rows_spec = [r for r in state.report.table_rows if r.cat1 == cat1]
    aliases = [r.item_id for r in rows_spec]
    groups = [g for g in state.groups if g.visible] or []
    header = [("그룹", [g.name for g in groups])] if groups else \
        [("전체", ["전체"])]

    per_group: list[dict[str, float | None]] = []
    for g in groups or [None]:
        sub = (state.data if g is None
               else state.data.filter(pl.col("gid") == g.gid))
        ws = wafer_stats(sub, state.hidden(), aliases, "avg")
        vals: dict[str, float | None] = {}
        for a in aliases:
            got = [v for per in ws.values.values()
                   if (v := per.get(a)) is not None]
            vals[a] = sum(got) / len(got) if got else None
        per_group.append(vals)

    ref = {}
    if opt.delta_vs_ref:
        g = state.ref_group()
        idx = next((i for i, x in enumerate(groups) if g and x.gid == g.gid), None)
        ref = per_group[idx] if idx is not None else {}

    rows = []
    for rs in rows_spec:
        rule = state.rf.by_alias.get(rs.item_id)
        rv = ref.get(rs.item_id)
        vals, offs = [], []
        for vg in per_group:
            v = vg.get(rs.item_id)
            off = not opt.delta_vs_ref and offspec(v, rule)
            if opt.delta_vs_ref and v is not None and rv is not None:
                v -= rv
            vals.append(v)
            offs.append(off)
        rows.append({"cats": rs.subcats, "item": rs.item_id,
                     "spec": spec_cells(rule), "values": vals, "offspec": offs})
    return TableData(cat1, header, rows,
                     cat_names=list(getattr(state.report, "cat_names", []) or []))


def to_tsv(td: TableData, opt: SummaryOptions | None = None) -> str:
    """클립보드용 TSV — 화면 표와 **같은 숫자**여야 한다.

    값은 build_table이 이미 Δ까지 반영해 둔 것을 그대로 쓴다. 제외 개수는
    셀에 붙이지 않고 마지막 캡션으로 넘긴다 — 엑셀에 붙였을 때 숫자로 남도록.
    """
    delta = bool(opt and opt.delta_vs_ref)
    labels = td.labels()                    # CAT2…CATn + item (개수는 템플릿이)
    lines = ["\t".join([""] * len(labels) + [lot for lot, ws in td.header_lots
                                             for _ in ws]),
             "\t".join(labels +
                       [wf for _, ws in td.header_lots for wf in ws])]
    for r in td.rows:
        lines.append("\t".join(td.label_values(r) +
                               [fmt_value(v, delta) for v in r["values"]]))
    if opt and opt.session_caption:
        lines += ["", opt.session_caption]
    return "\n".join(lines)


def number_format(v: float | None) -> str | None:
    """셀 하나의 표시 자릿수 — `model/specs.fmt_value`와 같은 경계(<1·≤10)."""
    if v is None:
        return None
    a = abs(v)
    return "0.000" if a < 1 else "0.00" if a <= 10 else "0.0"


def format_runs(values: list[float | None]) -> list[tuple[int, int, str]]:
    """값 목록 → `(시작, 끝, 서식)` 구간들. **빈 칸 구간은 빼고 돌려준다.**

    xlsx 내보내기가 셀마다 COM을 왕복하지 않게 하려는 것이다 — 한 item의 값은
    대개 자릿수가 같아 한 행이 구간 1~2개로 접힌다(wafer 43장이면 왕복 43회가
    1~2회로 준다).
    """
    fmts = [number_format(v) for v in values]
    return [(c0, c1, fmts[c0]) for c0, c1 in _runs(fmts) if fmts[c0] is not None]


def flag_runs(flags: list[bool]) -> list[tuple[int, int]]:
    """참인 구간만 `(시작, 끝)`으로 — 규격 이탈 셀 서식용."""
    return [(c0, c1) for c0, c1 in _runs(list(flags)) if flags[c0]]


def _runs(keys: list) -> list[tuple[int, int]]:
    """같은 값이 이어지는 구간의 (시작, 끝) 인덱스 — 세로 병합용.

    키에 상위 CAT을 포함해 넘기므로 **상위가 바뀌면 하위 병합도 끊긴다**(§3.3).
    """
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(keys) + 1):
        if i == len(keys) or keys[i] != keys[start]:
            out.append((start, i - 1))
            start = i
    return out


# ── xlsx (xlwings) ───────────────────────────────────────────
INVALID_SHEET_CHARS = r"[]:*?/\\"


def sheet_name(cat1: str, used: set[str]) -> str:
    """CAT1 → 엑셀이 받아 주는 시트 이름.

    CAT1은 사용자 템플릿 값이라 `/`나 `[]`가 섞이거나, 31자를 넘겨 잘린 뒤
    서로 겹칠 수 있다. 그대로 넘기면 COM 예외로 내보내기가 통째로 실패한다.
    """
    name = "".join("_" if ch in INVALID_SHEET_CHARS else ch
                   for ch in (cat1 or "표")).strip() or "표"
    name = name[:31]
    if name.casefold() not in used:
        used.add(name.casefold())
        return name
    for i in range(2, 1000):                      # 잘려서 겹치면 번호를 붙인다
        cand = f"{name[:31 - len(str(i)) - 1]}_{i}"
        if cand.casefold() not in used:
            used.add(cand.casefold())
            return cand
    raise ValueError(f"시트 이름을 만들 수 없습니다: {cat1}")


def export_xlsx(tables: list[TableData], path: str, opt: SummaryOptions) -> None:
    import xlwings as xw
    from xlwings.constants import HAlign, VAlign

    if not tables:
        raise ValueError("내보낼 표가 없습니다")
    RED_BG, RED_TX, HDR_BG, CAT_BG = 0xEEECFF, 0x1500D7, 0xF5F3F2, 0xFBFAFA
    used: set[str] = set()
    with xw.App(visible=False, add_book=False) as app:
        wb = app.books.add()
        for td in tables:
            sht = wb.sheets.add(sheet_name(td.name, used), after=wb.sheets[-1])
            n_w = sum(len(ws) for _, ws in td.header_lots)
            labels = td.labels()          # CAT2…CATn + item + 규격(§14)
            n_lab, n_spec = len(labels), len(td.spec_labels())
            vals = [td.label_values(r) for r in td.rows]
            # 제목
            sht["A1"].value = td.name
            sht["A1"].font.size, sht["A1"].font.bold = 14, True
            # 헤더 2행
            sht["A2"].value = [labels +
                               [lot for lot, ws in td.header_lots for _ in ws],
                               [""] * n_lab +
                               [wf for _, ws in td.header_lots for wf in ws]]
            c = n_lab + 1
            for _lot, ws in td.header_lots:             # lot 가로 병합
                if len(ws) > 1:
                    sht.range((2, c), (2, c + len(ws) - 1)).merge()
                c += len(ws)
            for col in range(1, n_lab + 1):             # 라벨 세로 병합
                sht.range((2, col), (3, col)).merge()
            hdr = sht.range((2, 1), (3, n_lab + n_w))
            hdr.color = HDR_BG
            hdr.font.bold = True
            hdr.api.HorizontalAlignment = HAlign.xlHAlignCenter
            # 본문
            body = [v + [None if x is None else round(x, 6) for x in r["values"]]
                    for v, r in zip(vals, td.rows)]
            sht["A4"].value = body
            # 자릿수 + 규격 이탈 — **셀 하나씩 만지지 않는다.**
            # xlwings의 `sht.range(...)` 접근과 속성 대입은 각각 COM 왕복이라,
            # wafer 43장 × item 60개면 5천 번을 넘고 멀티 lot(300열)이면 4만 번이
            # 된다. 같은 서식이 이어지는 구간(run)으로 묶으면 왕복이 행 수준으로
            # 줄어든다 — 한 item의 값은 대개 자릿수가 같아 행마다 1~2회다.
            for ri, r in enumerate(td.rows, start=4):
                for c0, c1, fmt in format_runs(r["values"]):
                    sht.range((ri, n_lab + 1 + c0),
                              (ri, n_lab + 1 + c1)).number_format = fmt
                for c0, c1 in flag_runs(r["offspec"]):
                    rng = sht.range((ri, n_lab + 1 + c0), (ri, n_lab + 1 + c1))
                    rng.color = RED_BG
                    rng.font.color = RED_TX
                    rng.font.bold = True
            # CAT 세로 병합 — item·규격 열은 빼고, 상위가 바뀌면 하위도 끊는다
            for col in range(1, n_lab - n_spec):
                for r0, r1 in _runs([tuple(v[:col]) for v in vals]):
                    if r1 > r0:
                        rng = sht.range((4 + r0, col), (4 + r1, col))
                        rng.merge()
                        rng.color = CAT_BG
                        rng.api.VerticalAlignment = VAlign.xlVAlignCenter
            # 테두리·너비·틀 고정·캡션
            full = sht.range((2, 1), (3 + len(td.rows), n_lab + n_w))
            for b in range(7, 13):
                full.api.Borders(b).Weight = 2
            for col in range(1, n_lab):
                sht.range((1, col), (1, col)).column_width = 14
            sht.range((1, n_lab), (1, n_lab)).column_width = 20   # item 열
            sht.range((1, n_lab + 1), (1, n_lab + n_w)).column_width = 9
            sht.range((4 + len(td.rows) + 1, 1)).value = opt.session_caption
            sht.api.Application.ActiveWindow.SplitRow = 3
            sht.api.Application.ActiveWindow.SplitColumn = n_lab
            sht.api.Application.ActiveWindow.FreezePanes = True
        # 기본 빈 시트 제거 — 표가 하나라도 있을 때만(엑셀은 시트 0개를 허용하지 않는다)
        if len(wb.sheets) > len(tables) >= 1:
            wb.sheets[0].delete()
        wb.save(path)
        wb.close()
