"""작은 공통 위젯 — 카드/섹션 헤더/보조 버튼."""
from __future__ import annotations

import contextlib

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

    # ── 접기 ──────────────────────────────────────────────────
    def make_collapsible(self, collapsed: bool = False) -> QPushButton:
        """제목 왼쪽에 ▾/▸ 토글을 붙인다. 만든 버튼을 돌려준다.

        Summary의 CAT1 표는 하나가 화면을 다 먹어서, 여러 CAT1을 비교하려면
        계속 스크롤해야 했다. 접어 두면 필요한 표만 펼쳐 볼 수 있다.
        표 자체는 그대로 두고 **보이기만** 바꾸므로 다시 계산하지 않는다.
        """
        btn = QPushButton()
        btn.setObjectName("collapseToggle")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedWidth(24)
        btn.setFlat(True)
        btn.toggled.connect(self.set_collapsed)
        if self.head is not None:
            self.head.insertWidget(0, btn)
        self._collapse_btn = btn
        btn.setChecked(collapsed)
        self.set_collapsed(collapsed)
        return btn

    def is_collapsed(self) -> bool:
        return bool(getattr(self, "_collapsed", False))

    def set_collapsed(self, on: bool) -> None:
        self._collapsed = bool(on)
        btn = getattr(self, "_collapse_btn", None)
        if btn is not None:
            btn.setText("▸" if on else "▾")
            if btn.isChecked() != bool(on):
                btn.blockSignals(True)
                btn.setChecked(bool(on))
                btn.blockSignals(False)
        for i in range(self.body.count()):
            w = self.body.itemAt(i).widget()
            if w is not None:
                w.setVisible(not on)


class SectionLabel(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName("sectionLabel")


class CollapsibleSection(QWidget):
    """제목 + ▾/▸ 로 접히는 묶음. 카드가 아니라 **구분선 없는 섹션**이라
    어두운 도크 안에서도 쓸 수 있다(Card는 흰 카드라 크롬 위에서 튄다).

    자주 쓰지 않는 도구를 접어 두면 도크가 같은 모양의 버튼 벽이 되지 않는다.
    """

    def __init__(self, title: str, collapsed: bool = True, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self._title = title.upper()
        self.toggle = QPushButton()
        self.toggle.setObjectName("sectionToggle")
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setFlat(True)
        v.addWidget(self.toggle)

        self._host = QWidget()
        self.body = QVBoxLayout(self._host)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(6)
        v.addWidget(self._host)

        self.toggle.toggled.connect(self.set_collapsed)
        self.toggle.setChecked(collapsed)
        self.set_collapsed(collapsed)

    def set_collapsed(self, on: bool) -> None:
        self.toggle.setText(f"{'▸' if on else '▾'}  {self._title}")
        self._host.setVisible(not on)


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
        self._shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(self._shadow)
        # **평소에는 꺼 둔다.** blur 0·offset 0이어도 효과가 켜져 있으면 위젯의
        # 사각 실루엣이 둥근 모서리 바깥으로 삐져나와 버튼 오른쪽에 회색 얼룩이
        # 남는다(리디자인 전 화면에 그대로 보였다).
        self._shadow.setEnabled(False)

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
        self._shadow.setEnabled(True)
        self._anim_blur.setStartValue(self._shadow.blurRadius())
        self._anim_blur.setEndValue(9)
        self._anim_offset.setStartValue(self._shadow.yOffset())
        self._anim_offset.setEndValue(2)
        self._anim_blur.start()
        self._anim_offset.start()

    def _lower(self) -> None:
        self._anim_blur.setStartValue(self._shadow.blurRadius())
        self._anim_blur.setEndValue(0)
        self._anim_offset.setStartValue(self._shadow.yOffset())
        self._anim_offset.setEndValue(0)
        # 애니메이션이 끝난 뒤에 꺼야 잔상 없이 사라진다
        self._anim_blur.finished.connect(self._shadow_off)
        self._anim_blur.start()
        self._anim_offset.start()

    def _shadow_off(self) -> None:
        with contextlib.suppress(RuntimeError, TypeError):   # 이미 끊겼다
            self._anim_blur.finished.disconnect(self._shadow_off)
        if not self.underMouse():
            self._shadow.setEnabled(False)


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
