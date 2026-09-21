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


def _tukey_default():
    """이상치 필터 설정의 기본값(꺼짐). 순환 import를 피해 함수로 둔다."""
    from etreport.model.outliers import TukeyConfig
    return TukeyConfig()


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
    # 이상치 필터(Tukey)가 걸러 낸 점 — key → {reason, item, at}.
    # **손으로 찍은 제외와 따로 둔다**: 필터를 끄면 이쪽만 비워야 하고,
    # 이력에도 "사람이 뺀 것"과 "규칙이 뺀 것"이 구분돼 남아야 한다.
    filtered: dict[str, dict] = field(default_factory=dict)
    tukey: object = field(default_factory=lambda: _tukey_default())

    log_patterns: list[str] = field(default_factory=lambda: ["Ioff*", "*Leak*", "Jg*"])
    agg: str = "avg"
    delta_vs_ref: bool = False
    table_slide_mode: str = "overflow"    # overflow | split (§7.3)
    explore: PlotSpec = field(default_factory=PlotSpec)
    db_label: str = "(DB 미연결)"
    store: object | None = None        # 쓰지 않음 — 연결은 열어 두지 않는다(loader.readonly_query)
    templates: object | None = None    # model.templates.Templates
    db_path: str = ""
    table: str = ""                    # 실제 조회 테이블 (기본 et_data)
    # 도크 lot 선택(§9.2) — lots_all은 DB에 있는 전부, lots_selected는 체크한 것.
    # **빈 리스트 = 전부**로 읽는다. 설정 파일이 없던 예전 상태와 새 상태가 같은
    # 뜻이 되어야 lot 선택을 모르는 DB에서도 지금까지처럼 동작한다.
    lots_all: list[str] = field(default_factory=list)
    lots_selected: list[str] = field(default_factory=list)
    lot_split_symbols: bool = False    # plot에서 lot마다 심볼을 달리할지(§5.2)
    # 측정 조건 필터(`model/conditions.py`) — step·site·temp로 **분석에 쓸
    # 데이터 자체**를 좁힌다. 빈 dict = 전체. 좁히기는 로딩 때 한 번만 걸리므로
    # `data`가 이미 좁혀진 프레임이고, 읽는 쪽은 아무것도 달리 하지 않아도 된다.
    cond_filter: dict[str, str] = field(default_factory=dict)
    # 콤보에 보여 줄 값 목록 — **좁히기 전** 프레임에서 만든다(좁힌 뒤에 만들면
    # 한 번 고른 값 말고는 목록에서 사라져 되돌릴 수 없다).
    cond_choices: dict[str, list[str]] = field(default_factory=dict)
    profile: object | None = None      # data.compat.TableProfile
    excl_points: dict = field(default_factory=dict)   # key → {reason, at}
    # 그룹 편집에서 손으로 배정한 것 — (lot, wafer, step, temp, site) → gid.
    # None은 '조건 무관'. [적용]으로 DB를 다시 읽어도 loader가 재적용한다.
    manual_groups: dict[tuple, str] = field(default_factory=dict)
    # inline 계측(기능 B) — 붙인 계측 열 이름과 top-k 결과
    met_columns: list[str] = field(default_factory=list)
    met_top: object | None = None      # polars DataFrame | None
    # 조회 결과 원본과 level — [적용]으로 DB를 다시 읽을 때 loader가 다시 붙인다.
    met_frame: object | None = None    # polars DataFrame | None
    met_level: str = "wafer"
    # fab tracking(기능 A)에서 뽑아 붙인 열 이름. 사용자가 이름을 정하므로
    # 코드에서 추측할 수 없다 — plot 축 후보·표 범주·PPT 슬라이드가 이 목록을 본다.
    track_columns: list[str] = field(default_factory=list)
    track_frame: object | None = None  # polars DataFrame | None (lot·wafer·열들)
    # 마지막으로 정한 컬럼 정의 — 창을 닫았다 열어도 그대로 보이게(§2).
    track_specs: list = field(default_factory=list)   # data.fabtracking.TrackColumn
    rf_path: str = ""
    rf_sheet: str | int = 0
    exclude_all_plots: bool = True     # 제외를 모든 plot에 적용할지
    # 상단 상태 레일이 읽는다. "" 면 상태 문구 없음(적용됨), 그 밖에는 그 문구를
    # 램프와 함께 띄운다 — 화면마다 따로 알리지 않고 여기 한 곳으로 모은다.
    status_note: str = ""
    applied: bool = False              # [적용]으로 읽은 뒤인가
    #: `active()`·`hidden_frame()` 캐시 — (프레임, 숨긴 키 집합, 결과).
    #: 직접 만지지 말 것.
    _active_cache: object | None = field(default=None, repr=False,
                                         compare=False)
    _hidden_cache: object | None = field(default=None, repr=False,
                                         compare=False)

    # ── 조회 ─────────────────────────────────────────────────
    def aliases(self) -> list[str]:
        return [r.alias for r in self.rf.rules]

    def value_columns(self) -> list[str]:
        """산점도 x·y가 될 수 있는 **숫자 열** — ALIAS + 붙여 둔 계측 열(§1).

        계측값은 ET item과 같은 자격의 숫자다(상관을 보려고 붙인다). 후보에
        넣지 않으면 [분석에 활용]을 눌러도 축에서 고를 수 없어 "적용이 안 된다".
        """
        from etreport.data.loader import item_columns

        base = self.aliases()
        if not base:
            base = item_columns(self.data) if self.data is not None else []
        cols = getattr(self.data, "columns", ())
        return base + [c for c in self.met_columns
                       if c in cols and c not in base]

    def group(self, gid: str) -> GroupStyle | None:
        return next((g for g in self.groups if g.gid == gid), None)

    def ref_group(self) -> GroupStyle | None:
        return next((g for g in self.groups if g.ref), None)

    def hidden(self) -> set[str]:
        """그림·표에서 빠질 점 전부 — 손으로 찍은 제외 ∪ 이상치 필터.

        **읽는 쪽은 전부 이걸 쓴다.** `excluded`만 보는 코드가 하나라도 남으면
        그 화면에서만 필터가 안 걸려 화면과 PPT의 숫자가 갈린다.
        찍고 지우는 쪽(클릭 제외)은 여전히 `excluded`를 직접 만진다.
        """
        return self.excluded | set(self.filtered)

    def active(self) -> pl.DataFrame:
        """제외·필터 반영된 포인트. **결과를 캐시한다.**

        리포트 미리보기는 슬롯 6개가 각자 이걸 부르고 `_excluded_frame()`이 또
        6번 부른다 — 20만 행에서 `is_in`을 12번 도는 자리였다.

        캐시 키는 `(프레임 동일성, 숨긴 키 집합)`이다. 버전 카운터를 쓰지 않는
        이유: `excluded`는 여러 곳에서 직접 add/discard되고, 개수만 보면 한 점을
        빼고 다른 점을 넣은 경우를 놓친다. 집합 비교는 숨긴 점 수(수십 개)에만
        비례하므로 필터 한 번보다 훨씬 싸다. 프레임은 polars 특성상 바뀔 때마다
        **새 객체**가 되므로(`with_columns`) 동일성 비교로 충분하다.
        """
        if self.data is None:
            return pl.DataFrame()
        hide = self.hidden()
        cached = self._active_cache
        if (cached is not None and cached[0] is self.data
                and cached[1] == hide):
            return cached[2]
        out = (self.data if not hide
               else self.data.filter(~pl.col("key").is_in(list(hide))))
        self._active_cache = (self.data, set(hide), out)
        return out

    def hidden_frame(self) -> pl.DataFrame | None:
        """그림에 회색 빈 심볼로 남길 점 — `active()`의 여집합.

        캐시 규칙도 같다. 리포트 미리보기는 슬롯마다 이걸 부르므로 캐시가 없으면
        `active()`와 짝을 이뤄 20만 행을 열두 번 훑는다.
        """
        if self.data is None:
            return None
        hide = self.hidden()
        if not hide:
            return None
        cached = self._hidden_cache
        if (cached is not None and cached[0] is self.data
                and cached[1] == hide):
            return cached[2]
        out = self.data.filter(pl.col("key").is_in(list(hide)))
        self._hidden_cache = (self.data, set(hide), out)
        return out

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
