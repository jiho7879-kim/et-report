"""AppState → pptgen.build_deck 브리지 — [PPT 생성] 버튼이 부르는 실제 경로.

실험별 반복: factor가 2개 이상이면 factor마다 그룹만 바꿔 전체 페이지를
반복 생성한다(확정 사양). factor 0~1개면 현재 그룹 그대로 1회.
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from etreport.model.specs import GroupStyle, PlotSpec
from etreport.model.state import AppState
from etreport.render import pptgen
from etreport.render.pptgen import TableData

log = logging.getLogger(__name__)

_ALL = GroupStyle(gid="", name="전체", color="#0071e3", symbol="o", size=6)


def _styles_for(state: AppState, exp: str):
    if exp and state.split is not None:
        return state.split.styles_for([exp])
    return state.groups or [_ALL]


def _assignment_for(state: AppState, exp: str) -> dict[tuple[str, str], str]:
    """키는 `model/wafers.key()` — W01·01·1 표기 차이로 배정이 비지 않게."""
    from etreport.model import wafers
    if exp and state.split is not None:
        return state.split.assignment([exp])
    if state.data is None:
        return {}
    return {wafers.key(lot, wf): gid for lot, wf, gid in
            zip(state.data["lot"], state.data["wafer"], state.data["gid"])}


def _plot_data(state: AppState, exp: str, spec: PlotSpec) -> dict[str, pl.DataFrame]:
    df = state.data
    if df is None:
        return {}
    hide = state.hidden()          # 손으로 찍은 제외 + 이상치 필터
    active = df.filter(~pl.col("key").is_in(list(hide))) if hide else df
    from etreport.model import wafers
    assign = _assignment_for(state, exp)
    gids = wafers.map_gids(active["lot"], active["wafer"], assign)
    active = active.with_columns(pl.Series("_g", gids))
    return {st.gid: active.filter(pl.col("_g") == st.gid)
            for st in _styles_for(state, exp)}


def _tables(state: AppState) -> list[TableData]:
    """CAT1별 TableData — 요약 탭과 완전히 같은 집계를 쓴다.

    실험(factor)과 무관하다. 표는 (lot, wafer)별 집계라 실험마다 다시 만들면
    같은 표가 중복될 뿐이다(§7.2). 분할(split)은 pptgen이 페이지를 만들 때 한다.
    """
    from etreport.export.excel import SummaryOptions
    from etreport.export.excel import build_table as _bt
    if state.report is None or state.data is None:
        return []
    # 화면(요약 탭)의 평균/산포·Δ 선택을 그대로 쓴다 — "화면 = 출력"
    opt = SummaryOptions(agg=state.agg, delta_vs_ref=state.delta_vs_ref)
    return [_bt(state, cat1, opt) for cat1 in state.report.table_names()]


def _group_tables(state: AppState) -> list[TableData]:
    """그룹별 평균 표 — 뒤쪽 페이지에 CAT1마다 한 장씩 붙는다.

    그룹이 없으면 만들지 않는다(열이 '전체' 하나뿐이라 볼 이유가 없다).
    """
    from etreport.export.excel import SummaryOptions
    from etreport.export.excel import build_table as _bt
    if state.report is None or state.data is None or not state.groups:
        return []
    opt = SummaryOptions(agg="gavg", delta_vs_ref=state.delta_vs_ref)
    out = []
    for cat1 in state.report.table_names():
        td = _bt(state, cat1, opt)
        td.name = f"{cat1} — 그룹별 평균"
        out.append(td)
    return out


def deck_meta(state: AppState) -> dict:
    """표지에 넣을 메타데이터 — 없는 항목은 비워 둔다(표지에서 줄째로 빠진다).

    TKOUT_TIME은 **마지막 측정 시각**을 쓴다. 분석 프레임에는 시각이 없으므로
    열려 있는 읽기 전용 연결에서 가볍게 한 번 조회한다.
    """
    df = state.data
    meta: dict[str, str] = {"title": state.report.report if state.report else
                            "ET Report"}
    if df is None or df.is_empty():
        return meta
    def _join(col: str, limit: int = 4) -> str:
        if col not in df.columns:
            return ""
        vals = [str(v) for v in dict.fromkeys(df[col].to_list()) if v not in
                (None, "")]
        head = ", ".join(vals[:limit])
        return head + (f" 외 {len(vals) - limit}개" if len(vals) > limit else "")

    meta["ROOT_LOT_ID"] = _join("lot")
    meta["STEP_ID"] = _join("step")
    temps = _join("temp")
    meta["TEMPERATURE"] = (temps + " ℃") if temps else ""
    db_path, prof = getattr(state, "db_path", None), getattr(state, "profile", None)
    roles = getattr(prof, "roles", {}) if prof is not None else {}
    wanted = [(label, role, roles[role]) for label, role in
              (("LINE_ID", "line"), ("TKOUT_TIME", "time")) if roles.get(role)]
    if db_path and wanted and Path(db_path).exists():
        # 분석 연결은 열어 두지 않는다 — 필요한 순간에만 열고 닫는다(loader)
        from etreport.data.loader import readonly_query
        try:
            with readonly_query(str(db_path)) as con:
                for label, role, col in wanted:
                    agg = "max" if role == "time" else "min"
                    try:
                        got = con.execute(
                            f'SELECT {agg}("{col}") FROM "{prof.table}"').fetchone()
                        meta[label] = ("" if got is None or got[0] is None
                                       else str(got[0]))
                    except Exception as e:         # noqa: BLE001 — 표지일 뿐이다
                        log.debug("표지 메타 조회 실패(%s): %s", label, e)
        except Exception as e:                     # noqa: BLE001
            log.debug("표지 메타용 DB 열기 실패: %s", e)
    return meta


def generate(state: AppState, out_path: str, on_progress=None) -> str:
    """`on_progress(done, total, 라벨)`을 주면 슬라이드마다 진행을 알린다.

    집계(표 만들기)와 저장은 슬라이드 수에 안 들어가므로 라벨로만 알린다 —
    분모를 흔들지 않으면서 "무엇을 하는 중인지"는 보이게 하기 위해서다.
    """
    experiments = state.factors if len(state.factors) > 1 else [""]
    from etreport.data.loader import exclusion_frame
    # 이상치 필터가 걸러 낸 점도 이력에 싣는다 — 덱을 받은 사람이 "무엇이 빠졌나"를
    # 알 수 있어야 한다(이유 문자열에 배수와 item이 적혀 있다).
    exlog = exclusion_frame(state, include_filtered=True)
    if on_progress:
        on_progress(0, 0, "표 집계 중")
    prs = pptgen.build_deck(
        report=state.report,
        experiments=experiments,
        group_styles_of=lambda exp: _styles_for(state, exp),
        plot_data_of=lambda exp, spec: _plot_data(state, exp, spec),
        tables=_tables(state),
        group_tables=_group_tables(state),
        rf=state.rf,
        log_patterns=state.log_patterns,
        exclusion_log=exlog,
        table_mode=state.table_slide_mode,
        factors=getattr(state, "met_top", None),   # inline 계측 top-k(기능 B)
        meta=deck_meta(state),                     # 표지
        split_rows=(state.split.wide if state.split is not None else None),
        # fab tracking에서 뽑아 붙인 컬럼(기능 A) — 이름은 사용자가 정한 그대로
        track_rows=getattr(state, "track_frame", None),
        lot_split=bool(getattr(state, "lot_split_symbols", False)),
        on_progress=on_progress,
    )
    p = Path(out_path)
    if p.suffix.lower() != ".pptx":
        p = p.with_suffix(".pptx")
    if on_progress:
        on_progress(0, 0, "파일 저장 중")
    prs.save(str(p))
    return str(p)
