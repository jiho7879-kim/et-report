"""AppState → pptgen.build_deck 브리지 — [PPT 생성] 버튼이 부르는 실제 경로.

실험별 반복: factor가 2개 이상이면 factor마다 그룹만 바꿔 전체 페이지를
반복 생성한다(확정 사양). factor 0~1개면 현재 그룹 그대로 1회.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

from etreport.model.specs import GroupStyle, PlotSpec
from etreport.model.state import AppState
from etreport.render import pptgen
from etreport.render.pptgen import TableData

_ALL = GroupStyle(gid="", name="전체", color="#0071e3", symbol="o", size=6)


def _styles_for(state: AppState, exp: str):
    if exp and state.split is not None:
        return state.split.styles_for([exp])
    return state.groups or [_ALL]


def _assignment_for(state: AppState, exp: str) -> dict[tuple[str, str], str]:
    if exp and state.split is not None:
        return state.split.assignment([exp])
    if state.data is None:
        return {}
    return {(lot, wf): gid for lot, wf, gid in
            zip(state.data["lot"], state.data["wafer"], state.data["gid"])}


def _plot_data(state: AppState, exp: str, spec: PlotSpec) -> dict[str, pl.DataFrame]:
    df = state.data
    if df is None:
        return {}
    active = df.filter(~pl.col("key").is_in(list(state.excluded))) \
        if state.excluded else df
    assign = _assignment_for(state, exp)
    gids = [assign.get((lot, wf), "") for lot, wf
            in zip(active["lot"], active["wafer"])]
    active = active.with_columns(pl.Series("_g", gids))
    return {st.gid: active.filter(pl.col("_g") == st.gid)
            for st in _styles_for(state, exp)}


def _tables(state: AppState) -> list[TableData]:
    """CAT1별 TableData — Summary 탭과 완전히 같은 집계를 쓴다.

    실험(factor)과 무관하다. 표는 (lot, wafer)별 집계라 실험마다 다시 만들면
    같은 표가 중복될 뿐이다(§7.2). 분할(split)은 pptgen이 페이지를 만들 때 한다.
    """
    from etreport.export.excel import SummaryOptions
    from etreport.export.excel import build_table as _bt
    if state.report is None or state.data is None:
        return []
    # 화면(Summary 탭)의 평균/산포·Δ 선택을 그대로 쓴다 — "화면 = 출력"
    opt = SummaryOptions(agg=state.agg, delta_vs_ref=state.delta_vs_ref)
    return [_bt(state, cat1, opt) for cat1 in state.report.table_names()]


def generate(state: AppState, out_path: str) -> str:
    experiments = state.factors if len(state.factors) > 1 else [""]
    from etreport.data.loader import exclusion_frame
    exlog = exclusion_frame(state)
    prs = pptgen.build_deck(
        report=state.report,
        experiments=experiments,
        group_styles_of=lambda exp: _styles_for(state, exp),
        plot_data_of=lambda exp, spec: _plot_data(state, exp, spec),
        tables=_tables(state),
        rf=state.rf,
        log_patterns=state.log_patterns,
        exclusion_log=exlog,
        table_mode=state.table_slide_mode,
    )
    p = Path(out_path)
    if p.suffix.lower() != ".pptx":
        p = p.with_suffix(".pptx")
    prs.save(str(p))
    return str(p)
