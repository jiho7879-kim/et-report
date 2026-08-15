"""비모달 알림 — 확인만 받는 모달 창을 대신한다.

"캐시를 비웠습니다", "복사했습니다"처럼 **읽고 나면 할 일이 없는** 알림은
모달이 아니어야 한다. 모달은 손을 멈추게 하고(마우스를 [확인]까지 옮겨야
한다), headless 테스트에서는 아예 영원히 멈춘다. 실패와 되돌릴 수 없는
확인은 그대로 QMessageBox로 남긴다 — 그건 정말로 손을 멈춰야 하는 일이다.

부모 위젯 아래쪽 가운데에 잠깐 떴다가 스스로 사라진다.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

DURATION_MS = 2600
FADE_MS = 220


class Toast(QLabel):
    def __init__(self, parent: QWidget, text: str) -> None:
        super().__init__(text, parent)
        self.setObjectName("toast")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)   # 클릭을 막지 않는다
        # 줄바꿈 있는 QLabel은 sizeHint를 정사각형에 가깝게 잡아 한 줄이면 될 문구를
        # 두세 줄로 접는다 — 글자 너비에서 직접 폭을 정한다.
        pad = 34
        want = self.fontMetrics().horizontalAdvance(text) + pad
        self.setFixedWidth(min(max(240, want), max(320, int(parent.width() * 0.7))))
        self.adjustSize()

        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(FADE_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def show_at_bottom(self) -> None:
        p = self.parentWidget()
        self.adjustSize()
        x = max(0, (p.width() - self.width()) // 2)
        self.move(x, max(0, p.height() - self.height() - 28))
        self.show()
        self.raise_()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        QTimer.singleShot(DURATION_MS, self._fade_out)

    def _fade_out(self) -> None:
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.deleteLater)
        self._anim.start()


def toast(parent: QWidget, text: str) -> Toast | None:
    """알림 한 줄. 부모가 화면에 없으면(테스트·종료 중) 조용히 건너뛴다."""
    if parent is None or not parent.isVisible():
        return None
    t = Toast(parent.window(), text)
    t.show_at_bottom()
    return t
