"""자동완성 QLineEdit — 목업 v8과 동일한 동작을 Qt로.

- 인라인 고스트(회색 뒤채움) + Tab/→ 확정
- ↓↑ 목록 + Enter
- 쉼표 구분 시 마지막 토큰만 완성 (멀티스캐터 입력)
- 순수 로컬 접두/부분 일치 — 외부 호출 없음
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QLineEdit


class _CommaCompleter(QCompleter):
    """쉼표 뒤 토큰만 대상으로 삼는 컴플리터."""

    def splitPath(self, path: str) -> list[str]:
        return [path.split(",")[-1].lstrip()]

    def pathFromIndex(self, index) -> str:
        picked = super().pathFromIndex(index)
        line: AutoCompleteEdit = self.widget()            # type: ignore[assignment]
        head, _, _ = line.text().rpartition(",")
        return f"{head}, {picked}" if head else picked


class AutoCompleteEdit(QLineEdit):
    def __init__(self, items_provider: Callable[[], list[str]],
                 comma: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._provider = items_provider
        self._model = QStringListModel(self)
        cls = _CommaCompleter if comma else QCompleter
        self._completer = cls(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)   # 부분 일치 목록
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self.setCompleter(self._completer)
        self.textEdited.connect(self._refresh)

    def _refresh(self, _text: str) -> None:
        self._model.setStringList(self._provider())

    def keyPressEvent(self, e) -> None:
        # Tab: 팝업의 첫 후보로 인라인 확정 (고스트 수동 구현 대신
        # currentCompletion을 사용 — Qt가 접두 일치를 우선 정렬한다)
        if e.key() in (Qt.Key_Tab, Qt.Key_Right) \
                and self.cursorPosition() == len(self.text()) \
                and self._completer.currentCompletion():
            head, _, _ = self.text().rpartition(",")
            pick = self._completer.currentCompletion()
            self.setText(f"{head}, {pick}" if head else pick)
            e.accept()
            return
        super().keyPressEvent(e)
