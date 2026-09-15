"""가로 페이지 스트립 — 리포트 페이지를 브라우저 탭처럼 늘어놓는다(설계 §2).

예전에는 세로 목록이 158px를 차지했다. 리포트 화면은 칼럼이 넷이라(도크 300 +
목록 158 + 캔버스 + 인스펙터 292) 1366×768에서 슬라이드가 616px밖에 못 썼다.
세로 36px만 쓰는 가로 스트립으로 바꿔 그 158px을 슬라이드에 돌려준다.

페이지 수는 3장일 수도 30장일 수도 있다. 그래서 브라우저 탭 방식이다:
칩을 나열하고, 넘치면 가로로 스크롤하며, `◀ ▶`로 한 칸씩 옮기고, `⌄`로 전체
목록을 팝업으로 본다. 어느 쪽이든 **세로 높이는 변하지 않는다.**
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QWidget,
)

from etreport.ui.tabs.common import detach

__all__ = ["PAGE_STRIP_HEIGHT", "PageStrip"]

PAGE_STRIP_HEIGHT = 36


class PageStrip(QWidget):
    """칩 나열 + 가로 스크롤 + `◀ ▶` + `⌄` 전체 목록.

    바깥에서 보는 API는 예전 `QListWidget`과 같은 뜻이다:
    `set_pages(labels, current)`로 채우고 `currentRowChanged`로 알린다.
    """

    currentRowChanged = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("pageStrip")
        self.setFixedHeight(PAGE_STRIP_HEIGHT)
        self._labels: list[str] = []
        self._row = -1
        self._chips: list[QPushButton] = []

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 3, 10, 3)
        h.setSpacing(6)

        self.btn_prev = self._nav("◀", -1, "이전 페이지")
        h.addWidget(self.btn_prev)

        # 칩 줄 — 넘치면 여기만 가로로 흐른다(세로 높이는 그대로)
        self._host = QWidget()
        self._row_lay = QHBoxLayout(self._host)
        self._row_lay.setContentsMargins(0, 0, 0, 0)
        self._row_lay.setSpacing(4)
        self._row_lay.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("pageStripScroll")
        self._scroll.setWidget(self._host)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        h.addWidget(self._scroll, 1)

        self.btn_next = self._nav("▶", 1, "다음 페이지")
        h.addWidget(self.btn_next)

        self.btn_all = QToolButton()
        self.btn_all.setObjectName("railMenuButton")
        self.btn_all.setText("⌄")
        self.btn_all.setCursor(Qt.PointingHandCursor)
        self.btn_all.setToolTip("페이지 전체 목록")
        self.btn_all.setPopupMode(QToolButton.InstantPopup)
        self._menu = QMenu(self.btn_all)
        self.btn_all.setMenu(self._menu)
        h.addWidget(self.btn_all)

    def _nav(self, text: str, step: int, tip: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("pageNav")
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.setFixedWidth(24)
        b.clicked.connect(lambda _c=False, s=step: self.step(s))
        return b

    # ── 목록 ─────────────────────────────────────────────────
    def set_pages(self, labels: list[str], current: int = 0) -> None:
        """페이지 라벨을 통째로 갈아 끼운다. 신호는 여기서 쏘지 않는다 —
        다시 그리는 쪽이 이미 알고 있다(되돌이 갱신 방지)."""
        self._labels = list(labels)
        while self._row_lay.count() > 1:              # 마지막 stretch는 남긴다
            it = self._row_lay.takeAt(0)
            if it.widget():
                detach(it.widget())
        self._chips = []
        for i, text in enumerate(self._labels):
            chip = QPushButton(text)
            chip.setObjectName("pageChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(text)
            chip.clicked.connect(lambda _c=False, r=i: self.set_current_row(r))
            self._row_lay.insertWidget(self._row_lay.count() - 1, chip)
            self._chips.append(chip)
        self._menu.clear()
        for i, text in enumerate(self._labels):
            act = self._menu.addAction(text)
            act.triggered.connect(lambda _c=False, r=i: self.set_current_row(r))
        self._row = -1
        self._apply_row(min(max(current, 0), len(self._labels) - 1)
                        if self._labels else -1, notify=False)

    def count(self) -> int:
        return len(self._labels)

    def current_row(self) -> int:
        return self._row

    def set_current_row(self, row: int) -> None:
        self._apply_row(row, notify=True)

    def step(self, delta: int) -> None:
        if not self._labels:
            return
        self.set_current_row(
            min(max(self._row + delta, 0), len(self._labels) - 1))

    def _apply_row(self, row: int, notify: bool) -> None:
        if row == self._row or not (0 <= row < len(self._labels)):
            self._sync_nav()
            return
        self._row = row
        for i, chip in enumerate(self._chips):
            chip.setChecked(i == row)
        # 고른 칩이 화면 밖이면 끌어온다(30장이어도 지금 보는 것이 보이게)
        if 0 <= row < len(self._chips):
            self._scroll.ensureWidgetVisible(self._chips[row], 40, 0)
        self._sync_nav()
        if notify:
            self.currentRowChanged.emit(row)

    def _sync_nav(self) -> None:
        self.btn_prev.setEnabled(self._row > 0)
        self.btn_next.setEnabled(0 <= self._row < len(self._labels) - 1)
        self.btn_all.setEnabled(bool(self._labels))
