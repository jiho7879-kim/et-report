"""hover 상태 픽셀 검증 — QSS를 실제로 로드해 hover 의사상태가 명세와 일치함을 확인.

app.py _load_style()과 동일하게 style.qss를 적용하고, QTest.mouseMove로
실제 :hover 의사상태를 트리거해 grab() 픽셀을 QSS 명세와 대조한다.
오프스크린 플랫폼에서 창이 (0,0)에 겹치는 문제를 피하려면 각 버튼을
y=220 간격으로 place()로 분리해야 한다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# ── QSS 스펙 기준 색상 (style.qss 셀렉터별) ──────────────────────────────
C = {
    # QPushButton { background:#0071e3; color:#ffffff; }
    "base_n": (0, 113, 227),
    # QPushButton:hover { background:#0077ed; }
    "base_h": (0, 119, 237),
    # QPushButton:disabled { background:#6b7280; color:#ffffff; }
    "base_d": (107, 114, 128),
    # QPushButton[dirty="true"] { background:#c26c00; }
    "dirty_n": (194, 108, 0),
    # QPushButton[dirty="true"]:hover { background:#b45309; }
    "dirty_h": (180, 83, 9),
    # QPushButton[ghost="true"] { background:#ffffff; color:#1d1d1f; }
    "ghost_n": (255, 255, 255),
    # QPushButton[ghost="true"]:hover { background:#f7f8fa; }
    "ghost_h": (247, 248, 250),
    # QPushButton[ghost="true"]:hover { border-color:#0071e3; }
    "ghost_hb": (0, 113, 227),
    # QPushButton[ghost="true"]:disabled { background:#6b7280; color:#ffffff; }
    "ghost_d": (107, 114, 128),
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
        """인접 상태: ghost disabled, base disabled."""
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
        assert not near(C["ghost_n"], C["ghost_d"], 1)
        assert not near(C["dirty_n"], (255, 149, 0), 1)
        assert not near(C["base_h"], C["dirty_h"], 1)

    def test_wcag_contrast(self, qapp):
        """WCAG AA (4.5:1) — B·C 수정 검증: dirty hover와 ghost disabled만 단언.

        나머지 대비는 정보 제공만(단언하지 않음).
        기본 hover는 4.32:1로 여전히 미달이지만 이번 작업 범위가 아니므로
        단언하지 않는다.
        """
        print("\n== 대비 (WCAG 4.5:1 권장) ==")
        for name, fg, bg in [
            ("기본 hover 흰글자/#0077ed", (255, 255, 255), C["base_h"]),
            ("dirty hover 흰글자/#b45309", (255, 255, 255), C["dirty_h"]),
            ("ghost hover 검정/#f7f8fa", (29, 29, 31), C["ghost_h"]),
            ("ghost disabled 흰글자/#6b7280", (255, 255, 255), C["ghost_d"]),
            ("기본 normal 흰글자/#0071e3", (255, 255, 255), C["base_n"]),
        ]:
            r = contrast(fg, bg)
            print(f"  {name}: {r:.2f}:1")

        print("\n== WCAG AA (4.5:1) — B·C 수정 검증 ==")
        # dirty hover #b45309
        r = contrast((255, 255, 255), C["dirty_h"])
        assert r >= 4.5, f"dirty hover #b45309 대비 {r:.2f}:1 < 4.5:1"
        print(f"  ✅ dirty hover #b45309: {r:.2f}:1 달성")

        # ghost disabled #6b7280
        r = contrast((255, 255, 255), C["ghost_d"])
        assert r >= 4.5, f"ghost disabled #6b7280 대비 {r:.2f}:1 < 4.5:1"
        print(f"  ✅ ghost disabled #6b7280: {r:.2f}:1 달성")
