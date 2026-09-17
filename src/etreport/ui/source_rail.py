"""왼쪽 소스 레일 — "무엇을 보고 있나"(설계 §1 규칙 1).

분석 **데이터셋을 정하는 것**만 여기 있다: 어떤 DB·템플릿·리포메터를 쓰는지,
어느 lot을 읽는지, 어떤 점을 버리는지(이상치), 어떤 컬럼을 덧붙이는지
(inline 계측·fab tracking). 이것들을 바꾸면 **볼 수 있는 것 자체가 달라진다.**
표현만 바꾸는 것(로그 축·lot 심볼·그룹 색)은 오른쪽 인스펙터로 갔다.

예전 도크는 10개 섹션짜리 스크롤 벽이었다(설계 §0 C). 줄인 방법:

| 무엇 | 어디로 | 왜 |
|---|---|---|
| `저장`·`새 이름`·`삭제` | 프리셋 옆 `⋯` 메뉴 | 몇 주에 한 번 쓰는 동작이 primary 색이었다 |
| `표시`(로그 패턴·lot 심볼) | 인스펙터 `[보기]` | 표현만 바꾼다 |
| `S3`·`SQL 조회`·`Excel 캐시` | 상단바 `[도구]` 메뉴 | 지금 보는 분석을 바꾸지 않는다 |
| `inline 계측`·`fab tracking` | `추가 소스` 절 | 컬럼을 붙여 **그릴 수 있는 것이 달라진다** |
| `제외 포인트` 큰 숫자 | 상태 레일 + 하단 요약 한 줄 | 같은 숫자가 두 곳이었다 |

**위젯은 이 클래스가 갖고 동작은 전부 워크스페이스(`owner`)가 한다** — 파일을
읽고 검증하는 일([적용])은 화면 조각이 아니라 세션의 일이고, 예약 실행 같은
다른 입구도 같은 코드를 타야 한다.

`[적용]`은 **스크롤 밖 하단에 고정**한다. 스크롤 안에 두면 목록을 내리는 순간
주 동작이 화면에서 사라진다(설계 §1 규칙 3의 레일 판).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from etreport.model import outliers
from etreport.ui.tabs.common import on_combo
from etreport.ui.widgets.cards import CollapsibleSection, GhostButton, SectionLabel

__all__ = ["FILES", "RAIL_WIDTH", "SourceRail"]

#: 레일 폭. 인스펙터 272와 합쳐 516이라 1366px에서 캔버스가 850px 남는다(설계 §2).
#: 안쪽 여백 12×2와 세로 스크롤바를 빼면 실제로 쓰는 자리는 208px다 — 여기
#: 들어가는 위젯은 그 폭에서 잘리지 않아야 한다(가로 스크롤이 없다).
RAIL_WIDTH = 244

#: 파일 5행 — (키, 라벨). **필수 여부는 여기서 정하지 않는다**: 판정은
#: `ui/guidance.py` 하나가 하고(설계 §5) 이 클래스는 그 결과를 그리기만 한다.
#: 두 곳에서 정하면 "표시는 초록인데 [적용]은 실패"가 된다.
FILES = (("db", "DB"),
         ("plot", "Plot"),
         ("tbl", "Table"),
         ("rfm", "리포메터"),
         ("split", "실험 조건"))


def _repolish(widget, **props) -> None:
    """속성을 바꾸고 스타일을 다시 입힌다 — 속성만 바꾸면 QSS가 다시 안 걸린다."""
    changed = False
    for name, value in props.items():
        if widget.property(name) != value:
            widget.setProperty(name, value)
            changed = True
    if changed:
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def _hairline() -> QFrame:
    line = QFrame()
    line.setObjectName("hline")
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    return line


class SourceRail(QWidget):
    """스크롤되는 소스 목록 + 스크롤 밖에 고정된 `[적용]` 블록."""

    def __init__(self, owner, parent=None) -> None:
        super().__init__(parent)
        self.owner = owner
        self.settings = owner.settings
        self.setObjectName("sourceRail")
        self.setFixedWidth(RAIL_WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        panel = QWidget()
        panel.setObjectName("dock")
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
        self._build_preset(v)
        self._build_files(v)
        self._build_factor(v)
        self._build_lot(v)
        self._build_tukey(v)
        self._build_extra_sources(v)
        v.addStretch(1)

        # 콤보는 기본이 "가장 긴 항목만큼"이라 항목 하나가 길어지면 레일 전체를
        # 밀어내고, 고정 폭 안에서는 그만큼 오른쪽이 잘려 나간다. 항목이 아니라
        # 자리에 맞춘다 — 긴 항목은 툴팁과 펼친 목록에서 읽는다.
        for cmb in panel.findChildren(QComboBox):
            cmb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cmb.setMinimumContentsLength(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        # 가로 스크롤은 두지 않는다 — 레일은 세로로만 흐른다. 켜져 있으면 폭을
        # 넘긴 위젯이 조용히 잘린 채로 남아 아무도 눈치채지 못한다.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("dockScroll")
        self.scroll, self.panel = scroll, panel
        outer.addWidget(scroll, 1)

        outer.addWidget(_hairline())
        outer.addWidget(self._build_footer())

    # ── 프리셋 ───────────────────────────────────────────────
    def _build_preset(self, v: QVBoxLayout) -> None:
        v.addWidget(SectionLabel("소스"))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.cfg_combo = QComboBox()
        on_combo(self.cfg_combo, lambda: self.owner._cfg_selected(
            self.cfg_combo.currentIndex()))
        row.addWidget(self.cfg_combo, 1)
        # `⋯` 하나에 모아 둔다 — 저장·새 이름·삭제는 한 번 정하면 몇 주에 한 번
        # 쓰는 동작인데 예전에는 primary 색 버튼으로 최상단을 차지했다.
        self.btn_cfg_menu = QToolButton()
        self.btn_cfg_menu.setObjectName("railMenuButton")
        self.btn_cfg_menu.setText("⋯")
        self.btn_cfg_menu.setCursor(Qt.PointingHandCursor)
        self.btn_cfg_menu.setToolTip("설정 프리셋 저장·새 이름·삭제")
        menu = QMenu(self.btn_cfg_menu)
        for text, fn in (("저장", lambda: self.owner._cfg_save()),
                         ("새 이름으로 저장…", lambda: self.owner._cfg_save_as()),
                         ("삭제…", lambda: self.owner._cfg_delete())):
            menu.addAction(text).triggered.connect(lambda _c=False, f=fn: f())
        self.btn_cfg_menu.setMenu(menu)
        self.btn_cfg_menu.setPopupMode(QToolButton.InstantPopup)
        row.addWidget(self.btn_cfg_menu)
        w = QWidget()
        w.setLayout(row)
        v.addWidget(w)

    # ── 파일 5행 (고르면 경로만 담아 둔다) ────────────────────
    def _build_files(self, v: QVBoxLayout) -> None:
        self._file_values: dict[str, QLabel] = {}
        self._file_labels: dict[str, tuple[QLabel, str]] = {}
        self._file_dots: dict[str, QLabel] = {}
        picks = {"db": lambda: self.owner._pick_db(),
                 "plot": lambda: self.owner._pick_tpl("plot"),
                 "tbl": lambda: self.owner._pick_tpl("table"),
                 "rfm": lambda: self.owner._pick_rfm(),
                 "split": lambda: self.owner._pick_split()}
        for key, label in FILES:
            b = self._file_row(key, label)
            b.clicked.connect(lambda _c=False, f=picks[key]: f())
            setattr(self, f"btn_{key}", b)
            v.addWidget(b)

    def _file_row(self, key: str, label: str) -> QPushButton:
        """파일 한 줄 — `●필수/○선택` · 라벨 · 값(모노). 눌러서 고른다.

        예전에는 5행이 전부 똑같아서 무엇이 없으면 [적용]이 실패하는지 알 수
        없었다(설계 §0 G). 필수/선택 표시와 "비어 있다"는 표시는
        `apply_guidance()`가 채운다 — 판정은 `ui/guidance.py`가 한다.
        """
        b = QPushButton()
        b.setObjectName("fileRow")
        b.setProperty("need", "false")
        b.setCursor(Qt.PointingHandCursor)
        b.setMinimumHeight(36)
        h = QHBoxLayout(b)
        h.setContentsMargins(9, 4, 9, 4)
        h.setSpacing(6)
        dot = QLabel("○")
        dot.setObjectName("fileNeed")
        self._file_dots[key] = dot
        lab = QLabel(label)
        lab.setObjectName("fileLabel")
        lab.setFixedWidth(56)
        self._file_labels[key] = (lab, label)
        val = QLabel()
        val.setObjectName("fileValue")
        val.setTextInteractionFlags(Qt.NoTextInteraction)
        h.addWidget(dot)
        h.addWidget(lab)
        h.addWidget(val, 1)
        self._file_values[key] = val
        return b

    def apply_guidance(self, reqs, guide_key: str = "") -> None:
        """필요 표시(층 1)와 가이드 강조(층 2)를 화면에 반영한다.

        판정은 하지 않는다 — `ui/guidance.py`가 준 목록을 그리기만 한다.
        색만으로 알리지 않는다: 모양(`●`/`○`) · 라벨의 `*` · 왼쪽 액센트 바 ·
        툴팁 넷이 함께 붙는다(dirty 표시와 같은 규칙).
        """
        by = {r.key: r for r in reqs}
        for key, _label in FILES:
            r = by.get(key)
            row = getattr(self, f"btn_{key}")
            need = bool(r) and not r.optional
            missing = bool(r) and need and not r.done
            dot = self._file_dots[key]
            dot.setText("●" if need else "○")
            dot.setToolTip("필수 — 없으면 [적용]이 실패합니다" if need
                           else "선택 — 없어도 [적용]됩니다")
            lab, text = self._file_labels[key]
            lab.setText(f"{text} *" if missing else text)
            row.setToolTip(r.how if (r and missing) else "")
            _repolish(row, need="true" if need else "false",
                      needs="true" if missing else "false",
                      guide="true" if key == guide_key else "false")
        for key, w in (("apply", self.btn_apply),
                       ("split", self.btn_split)):
            if key == "split":
                continue                      # 파일 행에서 이미 처리했다
            _repolish(w, guide="true" if key == guide_key else "false")

    def set_file(self, key: str, value: str, sheet: str = "") -> None:
        """파일 행의 값 갱신 — 비었으면 '고르기'를 흐리게 보여 준다."""
        lab = self._file_values[key]
        text = value or "고르기"
        if value and sheet:
            text = f"{value}  [{sheet}]"
        lab.setProperty("empty", "true" if not value else "false")
        # 레일 폭이 좁아 긴 파일명은 앞을 줄인다(끝의 이름·시트가 중요하다)
        fm = lab.fontMetrics()
        lab.setText(fm.elidedText(text, Qt.ElideLeft, max(lab.width(), 120)))
        lab.setToolTip(text if value else "")
        lab.style().unpolish(lab)
        lab.style().polish(lab)

    # ── lot 선택 (§9.2) ──────────────────────────────────────
    def _build_lot(self, v: QVBoxLayout) -> None:
        # 접혀 있어도 제목이 'LOT 3/12'로 상태를 말하므로 펼치지 않고도 안다.
        self.lot_section = CollapsibleSection("lot", collapsed=True)
        box = self.lot_section.body
        row = QHBoxLayout()
        for text, on in (("전체", True), ("해제", False)):
            b = GhostButton(text)
            b.clicked.connect(lambda _c=False, x=on: self.owner._lot_check_all(x))
            row.addWidget(b)
        row.addStretch(1)
        box.addLayout(row)

        # lot이 수십 개가 되면 120px 목록을 굴려 찾는 것이 일이다. 검색은
        # **숨기기만** 하고 체크는 건드리지 않는다 — 걸러 놓고 [적용]을 눌렀을 때
        # 안 보이던 lot이 조용히 빠지면 안 되기 때문이다(§9.2 '빈 리스트=전부').
        self.lot_search = QLineEdit()
        self.lot_search.setObjectName("lotSearch")
        self.lot_search.setPlaceholderText("lot 검색")
        self.lot_search.setClearButtonEnabled(True)
        self.lot_search.textChanged.connect(self.owner._lot_filter)
        box.addWidget(self.lot_search)

        self.lot_list = QListWidget()
        self.lot_list.setObjectName("lotList")
        self.lot_list.setFixedHeight(120)
        self.lot_list.itemChanged.connect(self.owner._lot_toggled)
        box.addWidget(self.lot_list)

        self.btn_coverage = GhostButton("커버리지")
        self.btn_coverage.setToolTip(
            "lot마다 item·wafer·측정 조건이 어떻게 다른지 표로 봅니다.\n"
            "기준은 item이 가장 많은 lot입니다.")
        self.btn_coverage.clicked.connect(lambda: self.owner._open_coverage())
        box.addWidget(self.btn_coverage)
        v.addWidget(self.lot_section)

    # ── 이상치 필터 ──────────────────────────────────────────
    def _build_tukey(self, v: QVBoxLayout) -> None:
        # lot 절과 같은 관용구 — 접힌 제목이 상태를 말한다('이상치 3.0 · 측정
        # 조건별'). 예전에는 펼쳐 두고도 스크롤 아래라 못 찾는 기능이었다.
        self.tukey_section = CollapsibleSection("이상치", collapsed=True)
        box = self.tukey_section.body
        self.chk_tukey = QCheckBox("IQR 밖 자동 제외")
        self.chk_tukey.setToolTip(
            "표·plot을 그리기 전에 사분위수 밖의 점을 걸러 냅니다.\n"
            "lo = Q1 − k×IQR · hi = Q3 + k×IQR\n"
            "걸러진 점은 버리지 않고 이력에 남고, 그림에는 회색 빈 심볼로 보입니다.")
        self.chk_tukey.toggled.connect(lambda _on: self.owner._tukey_changed())
        box.addWidget(self.chk_tukey)
        # 배수와 범위는 **두 줄로** 나눈다. 한 줄에 두면 '측정 조건별
        # (step · 온도)' 같은 긴 항목이 콤보의 최소 폭이 되어 레일 전체를
        # 밖으로 밀고, 그만큼 오른쪽 글자가 통째로 잘렸다.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("배수"))
        self.cmb_tukey_k = QComboBox()
        self.cmb_tukey_k.setEditable(True)      # 프리셋 밖의 값도 넣을 수 있게
        self.cmb_tukey_k.addItems([f"{k:g}" for k in outliers.PRESET_K])
        self.cmb_tukey_k.setToolTip(
            "IQR의 몇 배 밖을 이상치로 볼지. 3.0·4.5가 흔히 쓰는 값입니다.\n"
            "상자그림 수염(1.5)보다 크게 잡습니다 — 여기서 하는 일은 표시가\n"
            "아니라 '버리기'라, 1.5로 거르면 정상 산포의 꼬리까지 잘립니다.")
        self.cmb_tukey_k.currentTextChanged.connect(
            lambda _t: self.owner._tukey_changed())
        row.addWidget(self.cmb_tukey_k, 1)
        w = QWidget()
        w.setLayout(row)
        box.addWidget(w)
        self.cmb_tukey_scope = QComboBox()
        for key, label in outliers.SCOPE_LABELS.items():
            self.cmb_tukey_scope.addItem(label, key)
        self.cmb_tukey_scope.setToolTip(
            "사분위수를 어느 범위에서 구할지.\n"
            "25 ℃와 125 ℃를 한데 섞으면 정상적인 고온 측정이 통째로 이상치가\n"
            "되므로 기본은 측정 조건별입니다.")
        on_combo(self.cmb_tukey_scope, lambda: self.owner._tukey_changed())
        box.addWidget(self.cmb_tukey_scope)
        self.btn_tukey_log = GhostButton("걸러진 점 보기")
        self.btn_tukey_log.clicked.connect(lambda: self.owner._open_filter_log())
        box.addWidget(self.btn_tukey_log)
        v.addWidget(self.tukey_section)

    # ── 추가 소스 ────────────────────────────────────────────
    def _build_extra_sources(self, v: QVBoxLayout) -> None:
        """계측·tracking은 **데이터 쪽**이다 — 붙이면 boxplot X축·표 범주가 는다.

        펼침 여부는 설정에 남는다(예전 [도구] 절의 자리를 그대로 물려받는다).
        """
        sec = CollapsibleSection("추가 소스",
                                 collapsed=not self.settings.dock_tools_open)
        sec.toggle.toggled.connect(
            lambda on: setattr(self.settings, "dock_tools_open", not on))
        self.sources_section = sec
        for text, tip, fn in (
                ("inline 계측 불러오기",
                 "fab.f_fab_wf_met에서 계측값을 가져와 (lot, wafer)로 붙입니다.\n"
                 "붙인 값은 탐색 X축·요약에서 쓰고, 유의 인자 top-k는\n"
                 "PPT 슬라이드로 나갑니다.", lambda: self.owner._open_metrology()),
                ("fab tracking 불러오기",
                 "fab.f_fab_tracking에서 lot·wafer별 공정 조건을 가져옵니다.\n"
                 "line·process·part·기간은 분석 중인 DB에서 자동으로 채우고,\n"
                 "SQL은 직접 고칠 수 있습니다. 뽑을 컬럼의 이름을 정해 붙이면\n"
                 "boxplot X축·표 범주·PPT에서 그대로 쓸 수 있습니다.",
                 lambda: self.owner._open_fabtrack())):
            b = GhostButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, f=fn: f())
            sec.body.addWidget(b)
        self.lbl_sources = QLabel()
        self.lbl_sources.setObjectName("hint")
        self.lbl_sources.setWordWrap(True)
        sec.body.addWidget(self.lbl_sources)
        v.addWidget(sec)

    # ── 실험 factor ──────────────────────────────────────────
    def _build_factor(self, v: QVBoxLayout) -> None:
        """어떤 step을 실험 축으로 볼지 — **그룹이 만들어지는 근거**라 여기다.

        결과(그룹 목록·색·보이기)는 인스펙터 `[그룹]` 섹션이 갖는다. 근거는
        데이터 쪽, 결과의 표현은 표현 쪽 — 규칙 1을 그대로 따른다.
        """
        self.btn_factor = GhostButton("factor 편집")
        self.btn_factor.setToolTip(
            "조건이 갈리는 step 중 무엇을 실험 축으로 쓸지 고릅니다.\n"
            "고른 factor가 그룹을 만듭니다.")
        self.btn_factor.clicked.connect(lambda: self.owner._edit_split())
        v.addWidget(self.btn_factor)
        self.lbl_factor = QLabel()
        self.lbl_factor.setObjectName("hint")
        self.lbl_factor.setWordWrap(True)
        v.addWidget(self.lbl_factor)

    # ── 하단 고정 블록 ───────────────────────────────────────
    def _build_footer(self) -> QWidget:
        foot = QWidget()
        foot.setObjectName("railFooter")
        v = QVBoxLayout(foot)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(6)

        self.btn_apply = QPushButton("적용")
        self.btn_apply.setToolTip(
            "고른 파일들을 한 번에 읽고 검증합니다 (F5).\n"
            "파일을 고르는 동안에는 Excel을 열지 않습니다.")
        self.btn_apply.clicked.connect(lambda: self.owner.apply_config())
        v.addWidget(self.btn_apply)

        self.lbl_apply = QLabel("파일을 고르고 [적용]을 누르세요")
        self.lbl_apply.setObjectName("hint")
        self.lbl_apply.setWordWrap(True)
        v.addWidget(self.lbl_apply)

        # 건너뛴 행·확인할 것은 [적용]마다 모달로 띄우지 않는다 — 보고 싶을 때 연다.
        self.btn_apply_log = GhostButton("적용 결과 보기")
        self.btn_apply_log.setToolTip("리포메터·템플릿에서 건너뛴 행과 확인할 것을 봅니다")
        self.btn_apply_log.clicked.connect(lambda: self.owner._open_apply_log())
        self.btn_apply_log.setVisible(False)
        v.addWidget(self.btn_apply_log)

        # REPORT는 **콤보를 두지 않는다**(확정 사양 §5.1). 템플릿에 리포트가
        # 여럿이면 [적용] 뒤 문구로만 알린다.
        self.lbl_report = QLabel()
        self.lbl_report.setObjectName("hint")
        self.lbl_report.setWordWrap(True)
        v.addWidget(self.lbl_report)

        # 요약 한 줄 + 되돌리기. 제외 개수의 **큰 숫자는 상태 레일에 이미 있다** —
        # 같은 숫자를 두 곳에 두면 하나가 늦게 갱신될 때 어느 쪽이 맞는지 모른다.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.lbl_summary = QLabel()
        self.lbl_summary.setObjectName("hint")
        row.addWidget(self.lbl_summary, 1)
        self.btn_undo = GhostButton("되돌리기")
        self.btn_undo.setToolTip("마지막으로 제외한 점을 되살립니다 (Ctrl+Z)")
        self.btn_undo.clicked.connect(lambda: self.owner._undo())
        row.addWidget(self.btn_undo)
        w = QWidget()
        w.setLayout(row)
        v.addWidget(w)
        return foot
