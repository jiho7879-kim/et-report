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
    #: 버린 것은 없지만 알아 둬야 하는 것(lot 커버리지 등). warnings와 섞으면
    #: "제외 N건"으로 잘못 세어지고 볼 때마다 모달이 뜬다 — 성격이 다르다.
    notes: list[str] = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0

    def text(self, max_warn: int = 12) -> str:
        out = list(self.lines)
        if self.notes:
            out.append("")
            out.append(f"확인 {len(self.notes)}건:")
            out += [f"  {n}" for n in self.notes[:max_warn]]
            if len(self.notes) > max_warn:
                out.append(f"  … 외 {len(self.notes) - max_warn}건")
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


def apply_config(state: AppState, cfg: AnalysisConfig,
                 on_progress=None) -> LoadReport:
    """설정 하나를 상태에 적용. 예외를 던지지 않고 LoadReport로 돌려준다.

    `on_progress(done, total, 라벨)`을 주면 단계마다 부른다. 네 단계는 각각
    수 초~수십 초(Excel 실행·DuckDB 조회)라, 어디에서 기다리는지 보이지 않으면
    창이 멈춘 것과 구별되지 않는다. 분모는 **실제로 돌 단계 수**로 미리 센다.
    """
    rep = LoadReport()
    t0 = time.monotonic()

    stages = [bool(cfg.reformatter_path),
              bool(cfg.plot_template_path and cfg.table_template_path),
              bool(cfg.split_path or getattr(cfg, "split_text", "")),
              bool(cfg.db_path)]
    total = sum(stages)
    step = 0

    def tick(label: str) -> None:
        nonlocal step
        step += 1
        if on_progress:
            on_progress(step, total, label)

    # 1) 리포메터 ---------------------------------------------
    if cfg.reformatter_path:
        if on_progress:
            on_progress(step, total, "리포메터 읽는 중")
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
        tick("리포메터 완료")

    # 2) 템플릿 -----------------------------------------------
    if cfg.plot_template_path and cfg.table_template_path:
        if on_progress:
            on_progress(step, total, "템플릿 읽는 중")
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
        tick("템플릿 완료")

    # 3) 실험 조건 --------------------------------------------
    if cfg.split_path or getattr(cfg, "split_text", ""):
        if on_progress:
            on_progress(step, total, "실험 조건 읽는 중")
        try:
            from etreport.model.split import (
                BASELINE_DEFAULT,
                load_split_file,
                parse_split_text,
            )
            base = getattr(cfg, "split_baseline", "") or BASELINE_DEFAULT
            # 기준 lot을 정해 뒀으면 step별 기준을 그 lot에서 다시 뽑는다 —
            # 코드 하나로는 step마다 다른 기준을 적을 수 없다(§9.2).
            base_lot = getattr(cfg, "split_baseline_lot", "")
            state.split = (load_split_file(cfg.split_path, base,
                                           baseline_lot=base_lot)
                           if cfg.split_path
                           else parse_split_text(cfg.split_text, base, base_lot))
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
        tick("실험 조건 완료")
    elif state.split is not None:
        # **출처를 떼면 상태에서도 떨어져야 한다.** 예전에는 이 else가 없어서
        # 한 번 읽은 실험 조건이 설정에서 지워도 `state.split`에 그대로 남았고,
        # factor 그룹만 화면에 떠 있는 채로 되돌릴 길이 없었다.
        # 거기서 나온 그룹만 걷어 낸다 — 손으로 만든 그룹(그룹 편집·붙여넣기)은
        # split의 gid가 아니라서 그대로 살아남는다.
        from_split = ({g.gid for g in state.split.styles_for(state.factors)}
                      if state.factors else set())
        state.groups = [g for g in state.groups if g.gid not in from_split]
        state.split, state.factors = None, []
        rep.lines.append("실험 조건  연결 해제")

    # 4) DuckDB -----------------------------------------------
    # 측정 조건 필터는 **DB를 읽기 전에** 정한다 — 로딩이 그 조건으로 좁힌 프레임을
    # 돌려주므로, 표·plot·PPT는 아무것도 달리 하지 않아도 같은 범위를 본다.
    from etreport.model import conditions
    state.cond_filter = conditions.normalize({
        "step": getattr(cfg, "cond_step", ""),
        "site": getattr(cfg, "cond_site", ""),
        "temp": getattr(cfg, "cond_temp", "")})
    if cfg.db_path:
        if on_progress:
            on_progress(step, total, "DB 읽는 중")
        try:
            from etreport.data.loader import load_state
            rep.lines.append("DB  " + load_state(state, cfg.db_path,
                                                 lots=state.lots_selected))
        except Exception as e:                       # noqa: BLE001
            rep.ok = False
            rep.error = f"DB 열기 실패: {e}"
            return rep
        # lot마다 item·wafer·측정 조건이 갈리면 표와 plot이 조용히 어긋난다.
        # 자세한 내역은 [커버리지] 다이얼로그가 보여 주고, 여기서는 놓치지 않게
        # 요약 몇 줄만 남긴다(§9.2). **제외가 아니므로 notes로 간다.**
        try:
            from etreport.model.coverage import build as cov_build
            rep.notes += cov_build(state).lines()
        except Exception as e:                       # noqa: BLE001 — 부가 정보일 뿐이다
            log.debug("커버리지 요약 실패(무시): %s", e)
        tick("DB 완료")

    # 5) 이상치 필터 --------------------------------------------
    # DB를 읽은 **뒤** 건다 — 사분위수는 실제로 분석할 데이터에서 구해야 한다.
    # 표·plot은 이 결과를 반영한 상태에서 그려진다(요청: "그리기 전에 필터").
    from etreport.model.outliers import TukeyConfig
    from etreport.model.outliers import apply as tukey_apply
    state.tukey = TukeyConfig(
        enabled=bool(getattr(cfg, "tukey_enabled", False)),
        k=float(getattr(cfg, "tukey_k", 3.0) or 3.0),
        scope=str(getattr(cfg, "tukey_scope", "cond") or "cond"))
    if state.data is not None:
        try:
            res = tukey_apply(state)
            if state.tukey.enabled:
                # 버린 점이므로 notes가 아니라 warnings다 — 사용자가 세어야 한다.
                rep.warnings.append(f"[이상치] {res.summary()}")
        except Exception as e:
            log.warning("이상치 필터 실패(무시): %s", e, exc_info=True)
            rep.warnings.append(f"[이상치] 필터를 걸지 못했습니다: {e}")

    state.log_patterns = cfg.log_patterns or state.log_patterns
    state.lot_split_symbols = bool(getattr(cfg, "lot_split_symbols", False))
    from etreport.render.pptgen import table_mode_of  # 예전 값 'wide' 흡수
    state.table_slide_mode = table_mode_of(cfg.table_slide_mode)
    rep.elapsed = time.monotonic() - t0
    rep.lines.append(f"— {rep.elapsed:.1f}초")
    log.info("설정 적용 완료 (%.1fs)", rep.elapsed)
    return rep
