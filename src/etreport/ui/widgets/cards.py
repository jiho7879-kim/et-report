"""작은 공통 위젯 — 카드/섹션 헤더/보조 버튼."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class Card(QFrame):
    """흰 배경 둥근 카드. body 레이아웃에 내용을 채운다."""

    def __init__(self, title: str = "", sub: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)
        self._title_label = None
        if title:
            head = QHBoxLayout()
            lab = QLabel(title)
            lab.setObjectName("cardTitle")
            self._title_label = lab
            head.addWidget(lab)
            if sub:
                s = QLabel(sub)
                s.setObjectName("cardSub")
                head.addWidget(s)
            head.addStretch(1)
            self.head = head
            outer.addLayout(head)
        else:
            self.head = None
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)


    def setTitle(self, text: str) -> None:
        if self._title_label is not None:
            self._title_label.setText(text)


class SectionLabel(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName("sectionLabel")


class GhostButton(QPushButton):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("ghost", True)
        self.setCursor(Qt.PointingHandCursor)


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setObjectName("hline")
    return f


def row(*widgets, stretch_at: int | None = None) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for i, x in enumerate(widgets):
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, str):
            lay.addWidget(QLabel(x))
        else:
            lay.addWidget(x, 1 if i == stretch_at else 0)
    return w
