"""자동완성 QLineEdit — 목업 v8과 동일한 동작을 Qt로.

- 인라인 고스트(회색 뒤채움) + Tab/→ 확정
- ↓↑ 목록 + Enter
- 쉼표 구분 시 마지막 토큰만 완성 (멀티스캐터 입력)
- 순수 로컬 부분 일치 — 외부 호출 없음

**구분자는 무시하고 찾는다.** item ALIAS는 `Idsat N SVT` · `Idsat_N_SVT` ·
`idsat-n-svt`처럼 사람마다 다르게 적는다. 공백·`_`·`-`·`.`을 지운 뒤 비교하므로
`idsatn`만 쳐도 `Idsat N SVT`가 뜬다. `*`는 "그 사이에 뭐가 있어도 된다"로
읽는다(`Ids*svt`).
"""
from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QLineEdit

#: 비교할 때 지우는 구분자 — 이 글자들은 있으나 없으나 같은 것으로 본다
_SEPS = re.compile(r"[\s_\-.]+")


def squash(text: str) -> str:
    """비교용 키 — 구분자를 없애고 소문자로."""
    return _SEPS.sub("", text or "").casefold()


def matches(needle: str, hay: str) -> bool:
    """`needle`이 `hay`에 맞는가 — 구분자 무시 + `*` 와일드카드."""
    n, h = squash(needle), squash(hay)
    if not n:
        return True
    if "*" not in n:
        return n in h
    parts = [p for p in n.split("*") if p]
    pos = 0
    for p in parts:                       # 순서대로 나오면 맞는 것으로 본다
        found = h.find(p, pos)
        if found < 0:
            return False
        pos = found + len(p)
    return True


def rank(needle: str, items: list[str]) -> list[str]:
    """맞는 후보만, **앞에서 시작하는 것 우선**으로 정렬해 돌려준다."""
    n = squash(needle)
    hits = [it for it in items if matches(needle, it)]
    if not n or "*" in n:
        return hits
    return sorted(hits, key=lambda it: (not squash(it).startswith(n),
                                        len(it), it))


class _FilteredCompleter(QCompleter):
    """**걸러 내기는 우리가 한다** — Qt에는 이미 고른 후보만 넘긴다.

    Qt의 `MatchContains`는 글자 그대로만 본다. `_`나 공백을 무시하려면
    Qt가 한 번 더 거르지 못하게 해야 하므로 `splitPath`가 빈 접두어를
    돌려주게 하고(=모델 전체가 후보), 모델에는 `AutoCompleteEdit`가
    `rank()`로 고른 목록만 넣는다.
    """

    def __init__(self, model, comma: bool, parent=None) -> None:
        super().__init__(model, parent)
        self._comma = comma

    def splitPath(self, _path: str) -> list[str]:
        return [""]

    def pathFromIndex(self, index) -> str:
        picked = super().pathFromIndex(index)
        line: AutoCompleteEdit = self.widget()            # type: ignore[assignment]
        if not self._comma:
            return picked
        head, _, _ = line.text().rpartition(",")
        return f"{head}, {picked}" if head else picked


class AutoCompleteEdit(QLineEdit):
    def __init__(self, items_provider: Callable[[], list[str]],
                 comma: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._provider = items_provider
        self._comma = comma
        self._model = QStringListModel(self)
        self._completer = _FilteredCompleter(self._model, comma, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self.setCompleter(self._completer)
        self.textEdited.connect(self._refresh)

    def token(self) -> str:
        """지금 완성 대상인 토큰 (쉼표 모드면 마지막 것)."""
        text = self.text()
        return (text.split(",")[-1].strip() if self._comma else text.strip())

    def candidates(self) -> list[str]:
        """구분자·`*`를 감안해 걸러 낸 후보 목록(가까운 것부터)."""
        return rank(self.token(), list(self._provider()))

    def _refresh(self, _text: str) -> None:
        self._model.setStringList(self.candidates())

    def keyPressEvent(self, e) -> None:
        # Tab: 팝업의 첫 후보로 인라인 확정 (고스트 수동 구현 대신
        # currentCompletion을 사용 — Qt가 접두 일치를 우선 정렬한다)
        if e.key() in (Qt.Key_Tab, Qt.Key_Right) \
                and self.cursorPosition() == len(self.text()) \
                and self._completer.currentCompletion():
            # currentCompletion()은 pathFromIndex를 거치므로 쉼표 앞부분이
            # 이미 붙어 있다 — 여기서 또 붙이면 앞 토큰이 두 번 들어간다
            self.setText(self._completer.currentCompletion())
            e.accept()
            return
        super().keyPressEvent(e)
