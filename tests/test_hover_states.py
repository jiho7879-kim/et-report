"""버튼 상태 픽셀 검증 — QSS를 실제로 로드해 상태별 색이 토큰과 맞는지 본다.

app.py `_load_style()`과 같은 경로로 스타일을 적용하고, `QTest.mouseMove`로
진짜 `:hover`를 발화시켜 `grab()` 픽셀을 읽는다. 기대색은 **하드코딩하지 않고
`ui/theme.TOKENS`에서 가져온다** — 팔레트를 바꿨는데 테스트가 옛날 색을 붙잡고
있으면 검사가 의미를 잃는다. 여기서 고정하는 것은 "어떤 색인가"가 아니라
**상태마다 색이 다르고, 그 색이 토큰에서 온다**는 계약이다.

오프스크린 플랫폼에서는 창이 (0,0)에 겹쳐 커서가 첫 창에만 들어가므로
각 버튼을 y=220 간격으로 떨어뜨린다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from etreport.ui.theme import TOKENS


def rgb(token: str) -> tuple[int, int, int]:
    h = TOKENS[token].lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ── style.qss 셀렉터 → 토큰 ─────────────────────────────────────────────
C = {
    "base_n":  rgb("ACC"),        # QPushButton
    "base_h":  rgb("ACC_H"),      # QPushButton:hover
    "base_d":  rgb("DISABLED"),   # QPushButton:disabled
    "dirty_n": rgb("WARN"),       # [dirty="true"]
    "dirty_h": rgb("WARN_H"),     # [dirty="true"]:hover
    "ghost_n": rgb("PAPER"),      # [ghost="true"]
    "ghost_h": rgb("FIELD"),      # [ghost="true"]:hover
    "ghost_hb": rgb("ACC"),       # [ghost="true"]:hover 테두리
    "ghost_d": rgb("FIELD"),      # [ghost="true"]:disabled
}


# ── 공통 픽스처 ──────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    """offscreen QApplication + QSS 스타일 적용 (app.py _load_style()와 동일)."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    from etreport.app import _load_style
    _load_style(app)
    yield app
    app.processEvents()


# ── 헬퍼 ─────────────────────────────────────────────────────────────────
def mk(text, ghost=False, dirty=False, enabled=True):
    """QSS 속성을 부여한 QPushButton 생성."""
    from PySide6.QtWidgets import QPushButton

    b = QPushButton(text)
    if ghost:
        b.setProperty("ghost", True)
    if dirty:
        b.setProperty("dirty", "true")
    b.setEnabled(enabled)
    b.resize(120, 36)
    return b


_NEXT_Y = [0]


def place(w):
    """오프스크린에선 창이 (0,0)에 겹쳐 커서가 첫 창에만 들어가므로
    각 버튼을 y좌표 220씩 떨어뜨려 hover가 독립적으로 트리거되게 한다."""
    w.move(60, _NEXT_Y[0])
    _NEXT_Y[0] += 220


def sample(widget, fx=0.1, fy=0.5):
    """텍스트를 피해 좌측 안쪽(fx=0.1)을 샘플. 테두리는 fx=0.0."""
    img = widget.grab().toImage()
    x = int(img.width() * fx)
    y = int(img.height() * fy)
    return img.pixelColor(x, y).getRgb()[:3]


def hover(widget, on: bool, app):
    """hover 발화는 QTest.mouseMove로만 가능 (WA_UnderMouse/QEnterEvent로는
    실제 :hover가 트리거되지 않는다)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    if on:
        QTest.mouseMove(widget, QPoint(widget.width() // 2, widget.height() // 2))
    else:
        QTest.mouseMove(widget, QPoint(widget.width() + 60, widget.height() + 60))
    app.processEvents()


def near(a, b, tol=6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def lum(rgb):
    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = map(f, rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    l1, l2 = sorted((lum(a), lum(b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


# ── 검증 시나리오 ────────────────────────────────────────────────────────
class TestHoverStates:
    """hover 3종 (기본/dirty/ghost) 렌더링 검증."""

    def test_base_button_hover_cycle(self, qapp):
        """기본 버튼: normal → hover → 해제 복귀."""
        b = mk("기본")
        place(b)
        b.show()
        qapp.processEvents()
        assert near(sample(b), C["base_n"]), f"실측 {sample(b)} ≠ 기대 {C['base_n']}"
        hover(b, True, qapp)
        assert near(sample(b), C["base_h"]), f"실측 {sample(b)} ≠ 기대 {C['base_h']}"
        hover(b, False, qapp)
        assert near(sample(b), C["base_n"]), f"실측 {sample(b)} ≠ 기대 {C['base_n']}"

    def test_dirty_button_hover_cycle(self, qapp):
        """dirty 버튼: normal → hover → 해제 복귀."""
        d = mk("표 만들기", dirty=True)
        place(d)
        d.show()
        qapp.processEvents()
        assert near(sample(d), C["dirty_n"]), f"실측 {sample(d)} ≠ 기대 {C['dirty_n']}"
        hover(d, True, qapp)
        assert near(sample(d), C["dirty_h"]), f"실측 {sample(d)} ≠ 기대 {C['dirty_h']}"
        hover(d, False, qapp)
        assert near(sample(d), C["dirty_n"]), f"실측 {sample(d)} ≠ 기대 {C['dirty_n']}"

    def test_ghost_button_hover_cycle(self, qapp):
        """ghost 버튼: normal → hover(본문+테두리) → 해제 복귀."""
        g = mk("저장", ghost=True)
        place(g)
        g.show()
        qapp.processEvents()
        assert near(sample(g), C["ghost_n"]), f"실측 {sample(g)} ≠ 기대 {C['ghost_n']}"
        hover(g, True, qapp)
        assert near(sample(g), C["ghost_h"]), f"실측 {sample(g)} ≠ 기대 {C['ghost_h']}"
        # hover 시 테두리 색상도 확인 (fx=0.0은 테두리 영역)
        assert near(sample(g, fx=0.0), C["ghost_hb"], tol=25), (
            f"실측 {sample(g, fx=0.0)} ≠ 기대 {C['ghost_hb']}"
        )
        hover(g, False, qapp)
        assert near(sample(g), C["ghost_n"]), f"실측 {sample(g)} ≠ 기대 {C['ghost_n']}"

    def test_disabled_states(self, qapp):
        """비활성: 채운 버튼은 흐린 회색 면, ghost는 흐린 입력면."""
        gd = mk("중지", ghost=True, enabled=False)
        place(gd)
        gd.show()
        qapp.processEvents()
        assert near(sample(gd), C["ghost_d"]), f"실측 {sample(gd)} ≠ 기대 {C['ghost_d']}"

        bd = mk("기본", enabled=False)
        place(bd)
        bd.show()
        qapp.processEvents()
        assert near(sample(bd), C["base_d"]), f"실측 {sample(bd)} ≠ 기대 {C['base_d']}"

    def test_state_distinction(self, qapp):
        """상태 구분: 색상이 서로 구분 가능해야 한다."""
        assert not near(C["base_n"], C["base_h"], 1)
        assert not near(C["dirty_n"], C["dirty_h"], 1)
        assert not near(C["ghost_n"], C["ghost_h"], 1)
        assert not near(C["base_n"], C["base_d"], 1)
        # 미적용(앰버)과 주 동작(틸)은 한눈에 달라야 한다. 둘 다 흰 글자를
        # 받으므로 밝기는 비슷해도 되고, 갈라져야 하는 것은 색조다.
        assert not near(C["base_n"], C["dirty_n"], 40)
        assert not near(C["base_h"], C["dirty_h"], 1)

    def test_button_text_contrast(self, qapp):
        """버튼 글자 대비 — 활성 상태는 WCAG AA(4.5:1)."""
        paper = rgb("PAPER")
        for name, fg, bg in [
            ("주 버튼", paper, C["base_n"]),
            ("주 버튼 hover", paper, C["base_h"]),
            ("미적용 버튼", paper, C["dirty_n"]),
            ("미적용 hover", paper, C["dirty_h"]),
            ("ghost 본문", rgb("TEXT"), C["ghost_n"]),
            ("ghost hover", rgb("TEXT"), C["ghost_h"]),
        ]:
            r = contrast(fg, bg)
            assert r >= 4.5, f"{name} 대비 {r:.2f}:1 < 4.5:1"

    def test_disabled_text_contrast(self, qapp):
        """비활성 글자는 AA 예외지만, 읽을 수는 있어야 한다(3:1)."""
        for name, fg, bg in [
            ("채운 버튼 비활성", rgb("MUTED"), C["base_d"]),
            ("ghost 비활성", rgb("DIM"), C["ghost_d"]),
        ]:
            r = contrast(fg, bg)
            assert r >= 3.0, f"{name} 대비 {r:.2f}:1 < 3:1"
