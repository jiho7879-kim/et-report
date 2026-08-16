"""데모 모드 — 사내 DB·bdq·Excel 없이 **모든 기능**을 눌러 볼 수 있게 한다.

세 층으로 나뉜다.

  `demo_data`    무엇을 보여 줄지 (리포메터·템플릿·실험 조건·raw 측정값)
  `demo_bundle`  그것을 진짜 파일로 (DuckDB·리포메터·템플릿·실험 조건 csv/xlsx)
  `demo_sources` 사내 조회의 입구만 가짜로 (추출·계측·tracking·S3)

여기(`demo.py`)는 그 셋을 상태에 올리는 얇은 층이다.

  · `load_demo(state)`  — 파일 없이 즉시 화면이 그려지는 in-memory 한 벌
  · `prepare(...)`      — 번들을 만들고 [분석] 설정·[데이터] 프리셋에 꽂아,
                          [적용]·[추출]을 실제 코드로 돌려볼 수 있게 한다

실제 연결 시에는 `load_demo()` 대신
  state.rf     = reformatter.load(경로)
  state.report = templates.build_report(...)
  state.data   = loader.load_state(state, db_path)  (읽기 전용)
로 갈아끼우면 나머지 UI는 그대로 동작한다.
"""
from __future__ import annotations

import logging
from datetime import datetime

import polars as pl

from etreport import demo_data
from etreport.data.reformatter import Reformatter
from etreport.model.specs import GroupStyle, PlotSpec, ReportSpec
from etreport.model.split import SplitMatrix
from etreport.model.state import AppState

log = logging.getLogger(__name__)

DEMO_CONFIG = "데모"           # 분석 설정·추출 프리셋 이름
_DATA: pl.DataFrame | None = None      # 합성 프레임은 프로세스당 한 번만


# ── in-memory 한 벌 ─────────────────────────────────────────
def _reformatter() -> Reformatter:
    return demo_data.reformatter()


def _templates(rf: Reformatter):
    from etreport.model.templates import from_frames
    return from_frames(demo_data.plot_frame(), demo_data.table_frame(), rf)


def _report(rf: Reformatter, name: str = "M2_ET") -> ReportSpec:
    from etreport.model.templates import build_report
    return build_report(_templates(rf), name)


def _points() -> pl.DataFrame:
    """분석 프레임. 만드는 데 드는 시간이 아까워 프로세스당 한 번만 만든다."""
    global _DATA
    if _DATA is None:
        _DATA = demo_data.wide_frame()
    return _DATA.clone()


def load_demo(state: AppState) -> None:
    """상태 한 벌을 채운다 — 파일도 DB도 건드리지 않는다(테스트·시연 공용)."""
    rf = _reformatter()
    state.rf = rf
    state.templates = _templates(rf)
    state.reports = list(demo_data.REPORTS)
    state.report = _report(rf)
    state.split = SplitMatrix.from_dataframe(demo_data.split_frame())
    state.factors = ["M1"]
    state.data = _points()
    apply_split(state)
    state.explore = PlotSpec(x="Vtlin N SVT", y="Idsat N SVT")
    _seed_exclusions(state)
    state.db_label = "(데모 데이터)"
    state.applied = True
    state.status_note = ""


def _seed_exclusions(state: AppState) -> None:
    """제외 포인트 두 개를 미리 찍어 둔다 — 제외 이력 장표·복원(Ctrl+Z)용."""
    if state.data is None or state.data.height < 20:
        return
    at = datetime.now().isoformat(timespec="seconds")
    for key, why in zip(state.data["key"][:2], ("프로브 접촉 불량", "웨이퍼 가장자리")):
        state.excl_points[key] = {"reason": f"(데모) {why}", "at": at}
    state.excluded = set(state.excl_points)


def apply_split(state: AppState) -> None:
    """현재 factor로 그룹을 다시 만들고 포인트에 gid를 배정한다."""
    if state.split is None or state.data is None:
        return
    from etreport.model import wafers
    state.groups = state.split.styles_for(state.factors)
    assign = state.split.assignment(state.factors)
    gids = wafers.map_gids(state.data["lot"], state.data["wafer"], assign)
    state.data = state.data.with_columns(pl.Series("gid", gids))


def unassigned_groups(state: AppState) -> list[GroupStyle]:
    return [g for g in state.groups if g.visible]


# ── 번들까지 얹은 한 벌 (앱 부팅용) ─────────────────────────
def prepare(state: AppState, settings=None, root: str | None = None,
            rebuild: bool = False, sources: bool = True):
    """데모 모드 전체 준비 — 상태 + 번들 파일 + 설정 + 가짜 소스.

    화면은 `load_demo()`가 채운 in-memory 데이터로 **즉시** 뜨고, 도크의 파일
    칸에는 번들 경로가 꽂혀 있다. [적용](F5)을 누르면 그 파일들을 실제로 읽어
    같은 화면이 다시 그려진다 — 데모와 실사용의 경로가 갈리지 않는다.

    반환: 만들어진 `DemoBundle`(실패하면 None).
    """
    from etreport import demo_bundle

    load_demo(state)
    bundle = None
    try:
        bundle = demo_bundle.build(root, force=rebuild)
        if bundle.made:
            log.info("데모 번들 생성: %s (%s)", bundle.root, ", ".join(bundle.made))
        else:
            log.info("데모 번들 재사용: %s", bundle.root)
    except Exception as e:                       # noqa: BLE001 — 데모는 계속 뜬다
        log.warning("데모 번들을 만들지 못했습니다(%s) — in-memory 데이터만 씁니다", e)

    if sources:
        from etreport import demo_sources
        demo_sources.install(str(bundle.root / "_s3") if bundle else None)

    if settings is not None and bundle is not None:
        stage_settings(settings, bundle)
    return bundle


def stage_window(win) -> None:
    """창이 만들어진 뒤 데모용으로 손봐 주는 것 두 가지.

    ① [데이터] 화면의 기간을 **데모 데이터가 있는 날짜**로 맞춘다 — 기본값
       (최근 7일)으로 두면 합성 데이터가 없는 구간이라 추출이 0행으로 끝난다.
    ② 도크는 방금 꽂은 데모 설정을 '미적용'으로 표시한다. 화면에는 이미 데모
       데이터가 올라와 있으므로 무엇을 누르면 되는지 한 줄로 알려 준다.
    """
    from PySide6.QtCore import QDate

    data_ws = getattr(win, "data_ws", None)
    if data_ws is not None:
        data_ws.d_from.setDate(QDate(demo_data.D_FROM.year, demo_data.D_FROM.month,
                                     demo_data.D_FROM.day))
        data_ws.d_to.setDate(QDate(demo_data.D_TO.year, demo_data.D_TO.month,
                                   demo_data.D_TO.day))
    anal = getattr(win, "anal_ws", None)
    if anal is not None:
        anal.lbl_apply.setText("데모 데이터가 올라와 있습니다 — [적용](F5)을 누르면 "
                               "데모 번들 파일로 다시 읽습니다")


def stage_settings(settings, bundle) -> None:
    """[분석] 설정 · [데이터] 프리셋에 번들 경로를 꽂는다(맨 앞·기본 선택).

    데모 설정은 **저장하지 않는다** — 사용자의 settings.json에 데모 경로가
    남으면 다음 실사용에서 엉뚱한 파일을 가리킨다(app.py가 데모 모드에서
    설정 저장을 건너뛴다).
    """
    from etreport.config.settings import AnalysisConfig, Condition, ExtractPreset

    rf_path, rf_sheet = bundle.stage_reformatter()
    plot_p, plot_s, tbl_p, tbl_s = bundle.stage_templates()

    cfg = AnalysisConfig(
        name=DEMO_CONFIG, db_path=str(bundle.db),
        plot_template_path=plot_p, plot_sheet=plot_s,
        table_template_path=tbl_p, table_sheet=tbl_s,
        reformatter_path=rf_path, reformatter_sheet=rf_sheet,
        report="M2_ET", split_path=str(bundle.split_csv))
    settings.analysis_configs = [
        cfg, *[c for c in settings.analysis_configs if c.name != DEMO_CONFIG]]
    settings.last_analysis_config = DEMO_CONFIG

    preset = ExtractPreset(
        name=DEMO_CONFIG, db_path=str(bundle.root / "데모_추출.duckdb"),
        reformatter_path=rf_path, reformatter_sheet=rf_sheet,
        out_dir=str(bundle.root),
        conditions=[Condition("line_id", demo_data.LINE_ID, required=True),
                    Condition("step_id", "M2ET")])
    settings.extract_presets = [
        preset, *[p for p in settings.extract_presets if p.name != DEMO_CONFIG]]
    settings.last_extract_preset = DEMO_CONFIG
