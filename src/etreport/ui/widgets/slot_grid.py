"""슬롯 드래그 재배치 — plot 위치를 UI에서 바꾼다.

드래그&드롭으로 슬롯끼리 자리를 맞바꾼다(빈 슬롯으로 끌면 이동).
템플릿의 order 컬럼이 곧 화면 위치이므로, 여기서 바꾼 순서를 그대로
엑셀에 되쓸 수 있다(ReportTab의 [템플릿에 저장]).
"""
from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QFrame, QVBoxLayout

MIME = "application/x-etreport-slot"


class SlotFrame(QFrame):
    """슬롯 하나. 드래그 소스이자 드롭 타깃."""

    swapped = Signal(int, int)          # (from_index, to_index)
    picked = Signal(int)                # 클릭 선택

    def __init__(self, index: int, empty: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.empty = empty
        self.setObjectName("slot")
        self.setAcceptDrops(True)
        self.setProperty("selected", "false")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 4, 5, 5)
        lay.setSpacing(3)
        self.body = lay
        self._press = None

    # ── 드래그 소스 ──────────────────────────────────────────
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
            self.picked.emit(self.index)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._press is None or self.empty:
            return
        if (e.position().toPoint() - self._press).manhattanLength() < 12:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME, str(self.index).encode())
        drag.setMimeData(mime)
        drag.setPixmap(self.grab().scaledToWidth(180, Qt.SmoothTransformation))
        drag.exec(Qt.MoveAction)
        self._press = None

    # ── 드롭 타깃 ────────────────────────────────────────────
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
            self._highlight(True)

    def dragLeaveEvent(self, e) -> None:
        self._highlight(False)

    def dropEvent(self, e) -> None:
        self._highlight(False)
        if not e.mimeData().hasFormat(MIME):
            return
        src = int(bytes(e.mimeData().data(MIME)).decode())
        if src != self.index:
            self.swapped.emit(src, self.index)
        e.acceptProposedAction()

    def _highlight(self, on: bool) -> None:
        self.setProperty("dropTarget", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)


def swap_slots(slots: list, a: int, b: int) -> None:
    """페이지 슬롯 배열에서 두 자리를 맞바꾼다 (빈 슬롯 포함)."""
    slots[a], slots[b] = slots[b], slots[a]
