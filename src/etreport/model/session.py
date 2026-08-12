"""분석 설정의 '적용' 파이프라인 — 검증은 여기서 **딱 한 번**.

파일을 고를 때는 경로만 담아 두고(staging), [적용]을 누를 때 아래 순서로
한 번에 읽는다. 파일마다 골라 놓을 때마다 다시 읽고 검증하던 병목을 없앤다.

  1) 리포메터  (Excel 1회 · 캐시 적중 시 0회)
  2) 템플릿    (plot/table이 같은 파일이면 Excel 1회)
  3) 실험 조건 (선택)
  4) DuckDB    (읽기 전용)

각 단계 결과는 LoadReport에 모아 UI가 한 번에 보여준다.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from etreport.config.settings import AnalysisConfig
from etreport.model.state import AppState

log = logging.getLogger(__name__)


@dataclass
class LoadReport:
    ok: bool = True
    lines: list[str] = field(default_factory=list)     # 사용자에게 보여줄 요약
    warnings: list[str] = field(default_factory=list)  # 제외된 항목들
    error: str = ""
    elapsed: float = 0.0

    def text(self, max_warn: int = 12) -> str:
        out = list(self.lines)
        if self.warnings:
            out.append("")
            out.append(f"제외 {len(self.warnings)}건:")
            out += [f"  {w}" for w in self.warnings[:max_warn]]
            if len(self.warnings) > max_warn:
                out.append(f"  … 외 {len(self.warnings) - max_warn}건")
        if self.error:
            out.append("")
            out.append(self.error)
        return "\n".join(out)


def apply_config(state: AppState, cfg: AnalysisConfig) -> LoadReport:
    """설정 하나를 상태에 적용. 예외를 던지지 않고 LoadReport로 돌려준다."""
    rep = LoadReport()
    t0 = time.monotonic()

    # 1) 리포메터 ---------------------------------------------
    if cfg.reformatter_path:
        try:
            from etreport.data.reformatter import load as rf_load
            rf = rf_load(cfg.reformatter_path, cfg.reformatter_sheet or 0)
        except ImportError:
            rep.ok = False
            rep.error = "xlwings/Excel이 없는 환경입니다 — 사내 PC에서 실행하세요"
            return rep
        except Exception as e:                       # noqa: BLE001
            rep.ok = False
            rep.error = f"리포메터 읽기 실패: {e}"
            return rep
        if rf.errors:
            rep.ok = False
            rep.error = "리포메터 구조 오류:\n" + "\n".join(rf.report_lines()[:8])
            return rep
        state.rf = rf
        state.rf_path = cfg.reformatter_path
        state.rf_sheet = cfg.reformatter_sheet or 0
        rep.lines.append(
            f"리포메터  REAL {len(rf.reals())} · ADDP {len(rf.addps())}")
        rep.warnings += [f"[리포메터] {w.row}행 {w.alias}: {w.message}"
                         for w in rf.warnings]

    # 2) 템플릿 -----------------------------------------------
    if cfg.plot_template_path and cfg.table_template_path:
        try:
            from etreport.model.templates import build_report
            from etreport.model.templates import load as tpl_load
            t = tpl_load(cfg.plot_template_path, cfg.table_template_path,
                         state.rf, cfg.plot_sheet or 0, cfg.table_sheet or 0)
        except ImportError:
            rep.ok = False
            rep.error = "xlwings/Excel이 없는 환경입니다 — 사내 PC에서 실행하세요"
            return rep
        except Exception as e:                       # noqa: BLE001
            rep.ok = False
            rep.error = f"템플릿 읽기 실패: {e}"
            return rep
        if t.errors:
            rep.ok = False
            rep.error = "템플릿 구조 오류:\n" + "\n".join(t.report_lines()[:8])
            return rep
        state.templates = t
        state.reports = t.reports()
        report = cfg.report if cfg.report in state.reports else (
            state.reports[0] if state.reports else "")
        if report:
            state.report = build_report(t, report)
            cfg.report = report
            rep.lines.append(
                f"템플릿  {report} · {len(state.report.pages)}페이지 · "
                f"표 {len(state.report.table_names())}개")
        rep.warnings += [f"[{w.sheet}] {w.row}행: {w.message}" for w in t.warnings]

    # 3) 실험 조건 --------------------------------------------
    if cfg.split_path:
        try:
            from etreport.model.split import load_split_file
            state.split = load_split_file(cfg.split_path)
            if not state.factors:
                state.factors = state.split.steps[:1]
            rep.lines.append(
                f"실험 조건  {len(state.split.steps)}개 step · "
                f"factor {', '.join(state.factors)}")
            cf = state.split.confounds(state.factors)
            if cf:
                rep.warnings.append(
                    f"[실험] 혼입 {len(cf)}건 — '{cf[0].group}' 안에 "
                    f"{cf[0].step}가 섞여 있습니다")
        except Exception as e:                       # noqa: BLE001
            rep.warnings.append(f"[실험] 조건 파일을 읽지 못했습니다: {e}")

    # 4) DuckDB -----------------------------------------------
    if cfg.db_path:
        try:
            from etreport.data.loader import load_state
            rep.lines.append("DB  " + load_state(state, cfg.db_path))
        except Exception as e:                       # noqa: BLE001
            rep.ok = False
            rep.error = f"DB 열기 실패: {e}"
            return rep

    state.log_patterns = cfg.log_patterns or state.log_patterns
    from etreport.render.pptgen import table_mode_of  # 예전 값 'wide' 흡수
    state.table_slide_mode = table_mode_of(cfg.table_slide_mode)
    rep.elapsed = time.monotonic() - t0
    rep.lines.append(f"— {rep.elapsed:.1f}초")
    log.info("설정 적용 완료 (%.1fs)", rep.elapsed)
    return rep
