"""작은 공통 위젯 — 카드/섹션 헤더/보조 버튼."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


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
    """투명 배경 보조 버튼. hover 시 미세 리프트(그림자+상승) 효과."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("ghost", True)
        self.setCursor(Qt.PointingHandCursor)

        # hover 리프트 효과 — 레이아웃에 안전한 QGraphicsDropShadowEffect
        # (setGeometry/move 금지 → 그림자로 시각적 상승을 표현)
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(0)
        self._shadow.setYOffset(0)
        self._shadow.setColor(QColor(0, 0, 0, 80))
        self.setGraphicsEffect(self._shadow)

        # 애니메이션 객체는 인스턴스 하나씩 재사용 (매 hover마다 새로 만들지 않음)
        self._anim_blur = QPropertyAnimation(self._shadow, b"blurRadius")
        self._anim_blur.setDuration(120)
        self._anim_blur.setEasingCurve(QEasingCurve.OutCubic)

        self._anim_offset = QPropertyAnimation(self._shadow, b"yOffset")
        self._anim_offset.setDuration(120)
        self._anim_offset.setEasingCurve(QEasingCurve.OutCubic)

    def enterEvent(self, ev):
        if not self.isEnabled():
            return super().enterEvent(ev)
        self._lift()
        return super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._lower()
        super().leaveEvent(ev)

    def _lift(self) -> None:
        self._anim_blur.setStartValue(self._shadow.blurRadius())
        self._anim_blur.setEndValue(10)
        self._anim_offset.setStartValue(self._shadow.yOffset())
        self._anim_offset.setEndValue(2)
        self._anim_blur.start()
        self._anim_offset.start()

    def _lower(self) -> None:
        self._anim_blur.setStartValue(self._shadow.blurRadius())
        self._anim_blur.setEndValue(0)
        self._anim_offset.setStartValue(self._shadow.yOffset())
        self._anim_offset.setEndValue(0)
        self._anim_blur.start()
        self._anim_offset.start()


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
