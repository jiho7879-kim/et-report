"""하단 액션바 — 주 동작이 탭을 옮겨도 같은 자리에 있게 한다(설계 §1 규칙 3).

예전에는 탐색이 캔버스 위 툴바, 요약이 두 번째 왼쪽 칼럼, 리포트가 본문 상단에
주 동작을 두어서 탭을 옮길 때마다 누를 것을 눈으로 찾아야 했다. 이 바의
**왼쪽 끝이 주 동작의 영구 주소**이고(라벨만 `그리기`·`표 만들기`·`미리보기`로
바뀐다), 가운데는 상태 한 줄, 오른쪽 끝은 **결과를 꺼내는 자리**다 — 주 사용
루프 둘(요약표 공유·PPT)이 거기서 항상 한 번에 닿는다.

**버튼을 새로 만들지 않는다.** 탭이 이미 갖고 있는 위젯을 그대로 담는다.
프록시 버튼을 두면 dirty 표시(`•`)·활성 상태·`Ctrl+Enter`가 두 벌이 되어
조용히 갈린다(`tabs/common.StaleMixin`이 탭의 버튼을 직접 만진다).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtWidgets import (
    QHBoxLayout,
    QStackedWidget,
    QWidget,
)

__all__ = ["ACTION_BAR_HEIGHT", "ActionBar", "ActionItems"]

#: 바 높이. 1366×768 계산(설계 §2)에서 46+36+42 = 124를 빼고 본문이 644px다.
ACTION_BAR_HEIGHT = 42


@dataclass
class ActionItems:
    """탭이 액션바에 넘기는 것 — 탭마다 `action_items()`가 돌려준다.

    primary 는 탭의 주 동작 버튼(= `stale_button_attr`가 가리키는 그 버튼),
    status 는 상태 한 줄 라벨, extra 는 결과를 꺼내는 버튼들이다.
    """

    primary: QWidget
    status: QWidget | None = None
    extra: list[QWidget] = field(default_factory=list)


def _row(widgets) -> QWidget:
    """가로로 늘어놓는 한 칸. 빈 목록이어도 자리는 만든다(스택 페이지 수를 맞춘다)."""
    w = QWidget()
    h = QHBoxLayout(w)
    # 기본 여백 9px을 지우지 않으면 이 칸만 바 안쪽으로 들어가 버튼이 위아래로
    # 잘린다(42px 바에서는 9+9가 곧 글자 높이다).
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    for x in widgets:
        if x is not None:
            h.addWidget(x)
    return w


class ActionBar(QWidget):
    """탭마다 한 페이지 — 세 칸(주 동작 · 상태 · 결과)이 나란히 전환된다.

    세 칸을 각자 `QStackedWidget`으로 두는 이유: 스택의 sizeHint는 **모든
    페이지 중 가장 넓은 것**이라 주 동작 칸의 폭이 탭과 무관하게 일정하다.
    그래서 가운데 상태 줄과 오른쪽 버튼의 시작 자리도 탭을 옮겨도 움직이지
    않는다 — 한 페이지씩 따로 조립하면 라벨 길이에 따라 자리가 흔들린다.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("actionBar")
        self.setFixedHeight(ACTION_BAR_HEIGHT)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 4, 14, 4)
        h.setSpacing(12)
        self._primary = QStackedWidget()
        self._status = QStackedWidget()
        self._extra = QStackedWidget()
        h.addWidget(self._primary)
        h.addWidget(self._status, 1)
        h.addWidget(self._extra)

    # ── 페이지 ───────────────────────────────────────────────
    def add_page(self, items: ActionItems) -> int:
        """탭 하나를 등록하고 그 페이지 번호를 돌려준다(탭 순서와 같게 부른다)."""
        self._primary.addWidget(_row([items.primary]))
        self._status.addWidget(_row([items.status]))
        self._extra.addWidget(_row(list(items.extra)))
        return self._primary.count() - 1

    def show_page(self, index: int) -> None:
        for stack in (self._primary, self._status, self._extra):
            if 0 <= index < stack.count():
                stack.setCurrentIndex(index)

    def current_page(self) -> int:
        return self._primary.currentIndex()

    # ── 테스트·점검용 ────────────────────────────────────────
    def primary_of(self, index: int) -> QWidget | None:
        """그 페이지의 주 동작 버튼. 액션바가 탭의 버튼을 **그대로** 담았는지
        확인하는 자리다(프록시를 만들면 여기서 드러난다)."""
        page = self._primary.widget(index)
        if page is None:
            return None
        lay = page.layout()
        return lay.itemAt(0).widget() if lay and lay.count() else None
