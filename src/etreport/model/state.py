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
    table_slide_mode: str = "overflow"    # overflow | split (§7.3)
    explore: PlotSpec = field(default_factory=PlotSpec)
    db_label: str = "(DB 미연결)"
    store: object | None = None        # 읽기 전용 duckdb 연결
    templates: object | None = None    # model.templates.Templates
    db_path: str = ""
    table: str = ""                    # 실제 조회 테이블 (기본 et_data)
    # 도크 lot 선택(§9.2) — lots_all은 DB에 있는 전부, lots_selected는 체크한 것.
    # **빈 리스트 = 전부**로 읽는다. 설정 파일이 없던 예전 상태와 새 상태가 같은
    # 뜻이 되어야 lot 선택을 모르는 DB에서도 지금까지처럼 동작한다.
    lots_all: list[str] = field(default_factory=list)
    lots_selected: list[str] = field(default_factory=list)
    lot_split_symbols: bool = False    # plot에서 lot마다 심볼을 달리할지(§5.2)
    profile: object | None = None      # data.compat.TableProfile
    excl_points: dict = field(default_factory=dict)   # key → {reason, at}
    # 그룹 편집에서 손으로 배정한 것 — (lot, wafer, step, temp, site) → gid.
    # None은 '조건 무관'. [적용]으로 DB를 다시 읽어도 loader가 재적용한다.
    manual_groups: dict[tuple, str] = field(default_factory=dict)
    # inline 계측(기능 B) — 붙인 계측 열 이름과 top-k 결과
    met_columns: list[str] = field(default_factory=list)
    met_top: object | None = None      # polars DataFrame | None
    rf_path: str = ""
    rf_sheet: str | int = 0
    exclude_all_plots: bool = True     # 제외를 모든 plot에 적용할지
    # 상단 상태 레일이 읽는다. "" 면 상태 문구 없음(적용됨), 그 밖에는 그 문구를
    # 램프와 함께 띄운다 — 화면마다 따로 알리지 않고 여기 한 곳으로 모은다.
    status_note: str = ""
    applied: bool = False              # [적용]으로 읽은 뒤인가

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
        """표의 wafer 열 — **plot과 같은 기준**으로 좁힌다.

        plot은 visible 그룹의 gid만 그린다. 표만 DB 전체를 보여 주면 열이 수십
        개로 불어나고 화면과 출력이 갈라진다. 그래서 배정이 하나라도 있으면
        **visible 그룹에 속한 wafer만**, 배정이 없으면 전체를 보여 준다
        (그룹을 안 쓰는 흐름 — plot의 _ALL 폴백과 같다).

        한 wafer가 조건(step·온도)별로 다른 그룹에 걸릴 수 있으므로, **하나라도
        visible 그룹이면** 그 wafer 열을 포함한다.
        """
        if self.data is None:
            return []
        import re
        df = self.data
        if "gid" in df.columns and (df["gid"] != "").any():
            hidden = {g.gid for g in self.groups if not g.visible}
            df = df.filter((pl.col("gid") != "")
                           & ~pl.col("gid").is_in(list(hidden))
                           if hidden else pl.col("gid") != "")
            if df.is_empty():          # 전부 숨김이면 빈 표(열 없음)
                return []
        out: list[tuple[str, list[str]]] = []
        for lot in sorted(set(df["lot"])):
            ws = sorted(
                set(df.filter(pl.col("lot") == lot)["wafer"]),
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
    status_changed = Signal()      # 상단 상태 레일 (적용 여부·진행 문구)
