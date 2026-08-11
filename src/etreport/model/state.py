"""앱 전역 상태 — 위젯들이 공유하는 단일 진실.

UI 위젯은 여기서만 데이터를 읽고, 바꾼 뒤 changed 시그널을 쏜다.
DB가 연결되면 loader.load_state()가 data를 채우고, 그 전까지는
demo 모듈이 채워 넣은 샘플로 화면이 돈다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl
from PySide6.QtCore import QObject, Signal

from etreport.data.reformatter import Reformatter
from etreport.model.specs import GroupStyle, PlotSpec, ReportSpec
from etreport.model.split import SplitMatrix


@dataclass
class AppState:
    rf: Reformatter = field(default_factory=Reformatter)
    report: ReportSpec | None = None
    reports: list[str] = field(default_factory=list)
    split: SplitMatrix | None = None
    factors: list[str] = field(default_factory=list)
    groups: list[GroupStyle] = field(default_factory=list)

    # wide 포인트 테이블: key, lot, wafer, gid + <ALIAS>…
    data: pl.DataFrame | None = None
    excluded: set[str] = field(default_factory=set)
    undo_stack: list[str] = field(default_factory=list)

    log_patterns: list[str] = field(default_factory=lambda: ["Ioff*", "*Leak*", "Jg*"])
    agg: str = "avg"
    delta_vs_ref: bool = False
    table_slide_mode: str = "wide"
    explore: PlotSpec = field(default_factory=PlotSpec)
    db_label: str = "(DB 미연결)"
    store: object | None = None        # 읽기 전용 duckdb 연결
    templates: object | None = None    # model.templates.Templates
    db_path: str = ""
    table: str = ""                    # 실제 조회 테이블 (기본 et_data)
    profile: object | None = None      # data.compat.TableProfile
    excl_points: dict = field(default_factory=dict)   # key → {reason, at}
    rf_path: str = ""
    rf_sheet: str | int = 0
    exclude_all_plots: bool = True     # 제외를 모든 plot에 적용할지

    # ── 조회 ─────────────────────────────────────────────────
    def aliases(self) -> list[str]:
        return [r.alias for r in self.rf.rules]

    def group(self, gid: str) -> GroupStyle | None:
        return next((g for g in self.groups if g.gid == gid), None)

    def ref_group(self) -> GroupStyle | None:
        return next((g for g in self.groups if g.ref), None)

    def active(self) -> pl.DataFrame:
        """제외 반영된 포인트."""
        if self.data is None:
            return pl.DataFrame()
        if not self.excluded:
            return self.data
        return self.data.filter(~pl.col("key").is_in(list(self.excluded)))

    def wafer_columns(self) -> list[tuple[str, list[str]]]:
        if self.data is None:
            return []
        import re
        out: list[tuple[str, list[str]]] = []
        for lot in sorted(set(self.data["lot"])):
            ws = sorted(
                set(self.data.filter(pl.col("lot") == lot)["wafer"]),
                key=lambda s: [int(t) if t.isdigit() else t
                               for t in re.split(r"(\d+)", s)])
            out.append((lot, ws))
        return out


class StateBus(QObject):
    """상태 변경 알림 — 어느 화면에서 바꿔도 나머지가 따라 그린다."""
    data_changed = Signal()        # DB/데이터 교체
    groups_changed = Signal()      # 그룹·스타일·배정
    exclusion_changed = Signal()   # 제외/복원
    report_changed = Signal()      # 템플릿·Report 선택
    explore_changed = Signal()     # 탐색 축·옵션
