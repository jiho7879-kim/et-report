"""오른쪽 인스펙터 — "이미 정해진 데이터셋을 어떻게 보일까"(설계 §1 규칙 1).

왼쪽 레일이 **볼 수 있는 것 자체**를 정하고(DB·lot·이상치·추가 소스), 여기는
그것의 **표현**만 다룬다(축·스케일·그룹 색·표 집계·슬롯 배치). 새 컨트롤을
넣을 때 물을 것은 하나다 — "이게 데이터를 바꾸나, 표현을 바꾸나."

패널은 **워크스페이스가 하나만 소유한다.** 탭이 바뀌면 전용 섹션만 갈아
끼우고(`add_page`), 공용 섹션(그룹·보기)은 `shared`에 담겨 진짜 단일
인스턴스로 남는다 — 그룹 스타일 카드가 탐색·리포트에 한 벌씩 있던 것이
설계 §0 B(한 개념이 두세 군데)의 원인이었다. 규칙 2를 화면이 아니라 코드에서
지키기 위한 구조다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

__all__ = ["INSPECTOR_WIDTH", "InspectorPanel", "hairline"]

#: 인스펙터 폭. 왼쪽 레일 244 + 여기 272 = 516이라 1366px에서 캔버스가 850px
#: 남는다(설계 §2). 도크와 **같은 계약**이다 — 가로 스크롤이 없으므로 이 폭을
#: 넘긴 위젯은 오류 없이 오른쪽이 잘린 채로 남는다.
INSPECTOR_WIDTH = 272

#: 패널 안쪽 여백. **Card에는 덧대지 않는다** — Card가 이미 18px을 갖고 있어서
#: 양쪽에 또 주면 세로 스크롤바(12px)까지 합쳐 내용 폭이 232px로 줄고, 그룹
#: 스타일 카드(254px)가 조용히 잘린다. 여백을 한 곳에서 정하는 자리다.
EDGE = 14


def hairline() -> QFrame:
    """섹션 사이 1px 구분선 — QSS `#hline`(토큰 RULE)이 그린다."""
    line = QFrame()
    line.setObjectName("hline")
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    return line


def _edged(widget: QWidget) -> QWidget:
    """안쪽 여백을 덧댄다. Card는 스스로 여백을 가지므로 그대로 돌려준다."""
    from etreport.ui.widgets.cards import Card
    if isinstance(widget, Card):
        return widget
    box = QWidget()
    # 이름 없는 QWidget의 기본 세로 정책은 Preferred라 남는 높이를 나눠 갖는다 —
    # 그러면 섹션 사이가 화면 높이만큼 벌어져 콤보 하나가 패널 한가운데에 뜬다.
    box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    h = QHBoxLayout(box)
    h.setContentsMargins(EDGE, 0, EDGE, 0)
    h.setSpacing(0)
    h.addWidget(widget)
    return box


class InspectorPanel(QScrollArea):
    """탭 전용 섹션(스택) + 공용 섹션(그룹·보기)을 위아래로 쌓는다.

    도크와 같은 관용구: 고정 폭 스크롤 영역 + 가로 스크롤 금지. 가로 스크롤을
    켜 두면 폭을 넘긴 위젯이 조용히 잘린 채로 남아 아무도 눈치채지 못한다.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("inspectorScroll")
        self._panel = QWidget()
        self._panel.setObjectName("inspector")
        v = QVBoxLayout(self._panel)
        v.setContentsMargins(0, 12, 0, 14)
        v.setSpacing(0)

        head = QLabel("인스펙터")
        head.setObjectName("sectionLabel")
        head.setToolTip("표현을 다루는 자리입니다 — F10으로 접습니다")
        v.addWidget(_edged(head))

        self._stack = QStackedWidget()
        # 스택은 제 내용만큼만 — 남는 높이는 맨 아래 스트레치가 갖는다.
        self._stack.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        v.addWidget(self._stack)

        #: 공용 섹션 자리(그룹·보기) — 단계 3에서 채운다. 비어 있어도 자리는
        #: 지금 만든다: 나중에 만들면 탭마다 한 벌씩 다시 생기는 유혹이 남는다.
        self.shared = QVBoxLayout()
        self.shared.setContentsMargins(0, 0, 0, 0)
        self.shared.setSpacing(0)
        v.addLayout(self.shared)
        v.addStretch(1)

        self.setWidget(self._panel)
        self.setWidgetResizable(True)
        self.setFixedWidth(INSPECTOR_WIDTH)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)

    # ── 탭 전용 섹션 ─────────────────────────────────────────
    def add_page(self, sections) -> int:
        """탭 하나의 섹션 묶음을 등록하고 페이지 번호를 돌려준다.

        구분선은 여기서 넣는다 — 탭이 각자 넣으면 첫 섹션 위나 마지막 아래에
        선이 하나씩 남는 차이가 생긴다.
        """
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        for i, w in enumerate(sections):
            if i:
                pv.addWidget(hairline())
            pv.addWidget(_edged(w))
        pv.addStretch(1)          # 짧은 페이지의 섹션이 세로로 늘어나지 않게
        self._stack.addWidget(page)
        self._fit_combos()
        return self._stack.count() - 1

    def show_page(self, index: int) -> None:
        if 0 <= index < self._stack.count():
            self._stack.setCurrentIndex(index)

    def add_shared(self, widget: QWidget) -> None:
        """탭과 무관하게 늘 보이는 섹션(그룹·보기)."""
        if self.shared.count():
            self.shared.addWidget(hairline())
        self.shared.addWidget(_edged(widget))
        self._fit_combos()

    def panel(self) -> QWidget:
        """내용 위젯 — 폭 계약을 검사하는 테스트가 본다."""
        return self._panel

    # ── 폭 계약 ──────────────────────────────────────────────
    def _fit_combos(self) -> None:
        """콤보는 기본이 "가장 긴 항목만큼"이라 항목 하나가 길어지면 패널
        전체를 밀어내고 그만큼 오른쪽이 잘린다. 항목이 아니라 자리에 맞춘다 —
        긴 항목은 툴팁과 펼친 목록에서 읽는다(도크와 같은 처리)."""
        for cmb in self._panel.findChildren(QComboBox):
            cmb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cmb.setMinimumContentsLength(6)
