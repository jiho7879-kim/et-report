"""시각 토큰 계약 — style.qss가 토큰만으로 완성되고, 대비가 AA를 넘는지.

리디자인에서 실제로 무너지기 쉬운 세 가지를 고정한다.
  ① 치환 자리(%TOKEN%)가 남지 않는다 — 남으면 그 규칙이 통째로 무시된다.
  ② 글자/배경 짝이 전부 WCAG AA(4.5:1). 예전 팔레트는 보조 문구가 2.3:1,
     주 버튼이 4.32:1로 미달이었고 그게 "낡아 보인다"의 절반이었다.
  ③ 예전에 OS 기본 위젯으로 새던 자리(스크롤바·체크박스·포커스)를 QSS가
     실제로 덮고 있다.
"""
from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from etreport.ui import theme


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb) -> float:
    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = map(f, rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    l1, l2 = sorted((_lum(_rgb(a)), _lum(_rgb(b))), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def rendered(icons=None) -> str:
    return theme.render_qss('"UI"', '"Mono"', icons)


# ── ① 치환 ───────────────────────────────────────────────────────────────
def test_no_placeholder_left():
    """아이콘까지 포함해 %NAME% 자리가 하나도 남지 않는다."""
    css = rendered(theme.icon_dir())
    left = re.findall(r"%[A-Z_]+%", css)
    assert not left, f"치환되지 않은 자리: {sorted(set(left))}"


def test_runs_without_icons():
    """아이콘을 못 만드는 환경에서도 스타일시트가 깨지지 않는다.

    image 규칙은 줄 단위로 빠지므로, 그 줄만 지워도 여는 중괄호가 남지 않아야
    한다(남으면 뒤따르는 규칙이 전부 먹통이 된다).
    """
    css = rendered(None)
    assert "%ICONS%" not in css
    assert css.count("{") == css.count("}")


def test_tokens_cover_qss():
    """QSS가 쓰는 토큰이 전부 표에 있다(오타로 규칙이 죽는 것을 막는다)."""
    raw = theme.qss_path().read_text(encoding="utf-8")
    used = set(re.findall(r"%([A-Z_0-9]+)%", raw))
    known = set(theme.TOKENS) | {"UI_FONT", "MONO_FONT", "ICONS"}
    assert used <= known, f"표에 없는 토큰: {sorted(used - known)}"


# ── ② 대비 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,fg,bg", theme.CONTRAST_PAIRS)
def test_contrast_aa(name, fg, bg):
    r = contrast(theme.TOKENS[fg], theme.TOKENS[bg])
    assert r >= 4.5, f"{name}: {fg} on {bg} = {r:.2f}:1 < 4.5:1"


def test_dim_is_readable_enough():
    """흐린 글자(플레이스홀더·비활성)는 AA 예외지만 3:1은 넘긴다."""
    assert contrast(theme.TOKENS["DIM"], theme.TOKENS["FIELD"]) >= 3.0
    assert contrast(theme.TOKENS["DIM"], theme.TOKENS["PAPER"]) >= 3.0


def test_chrome_and_paper_are_separated():
    """크롬과 측정면은 확실히 다른 면이어야 한다(둘이 붙으면 경계가 사라진다)."""
    assert contrast(theme.TOKENS["INK"], theme.TOKENS["PAPER"]) > 10


# ── ③ 덮는 범위 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("selector", [
    "QScrollBar:vertical",          # 예전엔 규칙이 없어 OS 기본 스크롤바가 났다
    "QScrollBar::add-line",         # 화살표 제거
    "QCheckBox::indicator",
    "QComboBox QAbstractItemView",  # 콤보 팝업
    "QMenu::item",
    "QToolTip",
    "QHeaderView::section",
    "QTabBar::tab:selected",
    "QPushButton:focus",            # 키보드 포커스
    "QLineEdit:focus",
    "#statusRail",
    "#fileRow",
    "#toast",
])
def test_qss_covers(selector):
    assert selector in theme.qss_path().read_text(encoding="utf-8")


def test_no_global_widget_background():
    """전역 QWidget 배경 규칙은 두지 않는다 — 어두운 도크가 얼룩진다."""
    for line in theme.qss_path().read_text(encoding="utf-8").splitlines():
        if line.startswith("QWidget {"):
            assert "background" not in line, line


# ── 적용 ─────────────────────────────────────────────────────────────────
def test_apply_sets_fusion_and_stylesheet():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    # 스타일시트를 걸면 Qt가 실제 스타일을 QStyleSheetStyle로 감싸므로
    # style().name()으로는 바탕 스타일을 알 수 없다 — apply()가 남긴 값을 본다.
    assert app.property("etreport_base_style").lower() == "fusion"
    assert app.style().metaObject().className() == "QStyleSheetStyle"
    assert "#statusRail" in app.styleSheet()
    assert app.palette().highlight().color().name().lower() == \
        theme.TOKENS["ACC"].lower()
