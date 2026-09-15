"""시각 토큰 단일 진실 — 색·모서리·글자 크기와 그것을 Qt에 거는 방법.

방향은 **라이트 크롬 + 종이 측정면**이다. 상단바·도크는 밝은 무채색(크롬,
은은한 보라 틴트 — `design-plans/calm-accent-violet.md`), 카드·표·캔버스는
종이 흰색(측정면)으로 나눈다. "데이터는 여전히 잉크, 크롬은 이제 종이" —
위계는 어둠이 아니라 hairline 경계선과 표면 명도로 표현한다.
색은 "알아채야 하는 상태"에만 쓴다 — 차트가 이미 Okabe-Ito 8색
(`model/split.py`)과 규격 빨강을 쓰고 있어서, UI가 같은 색조로 경쟁하면 눈이
데이터로 가지 않는다. 액센트가 보라인 이유도 그것이다(차트·타깃 파랑이 쓰지
않는 색조 — 차분한 보라 #6D5BD0, 흰 글자 대비 5.18:1).

여기 있는 값이 `style.qss`의 `%TOKEN%` 자리로 들어간다. **색을 코드나 QSS에
직접 적지 말고 반드시 이 표에 넣는다** — `tests/test_theme.py`가 대비(WCAG AA)를
이 표 기준으로 검사하므로, 값을 두 곳에 두면 검사가 무의미해진다.

Fusion 스타일을 강제하는 이유: 스타일을 지정하지 않으면 Windows에서는 네이티브
스타일이 스크롤바·체크박스·콤보 화살표를 자기 식으로 그리고 나머지만 QSS가
칠해서, 한 화면 안에 두 시대의 위젯이 섞인다. Fusion은 QSS를 그대로 따르고
Windows/리눅스에서 같은 결과를 준다(오프스크린 캡처 검증이 의미를 갖는다).
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# ── 토큰 ──────────────────────────────────────────────────────────────────
TOKENS: dict[str, str] = {
    # 크롬 — 밝은 무채색(라이트 크롬). 상단바·도크.
    "INK":       "#F5F6F9",     # 은은한 보라 틴트 (calm-accent-violet)
    "INK_2":     "#FFFFFF",     # 크롬 위 입력면·올린 표면
    "INK_3":     "#E9EAF0",     # 크롬 위 hover (보라 틴트)
    "INK_LINE":  "#E1E2E9",     # 크롬 hairline (보라 틴트)
    "INK_TEXT":  "#1B2027",
    "INK_MUTED": "#5F6874",     # INK 대비 5.3:1

    # 크롬 위 신호색 — 어두운 크롬이 사라져 "한 색조를 밝게 뒤집는" 일이
    # 필요 없으므로 측정면 신호색과 같은 값으로 단일화한다.
    "ACC_INK":  "#6D5BD0",
    "OK_INK":   "#136B3A",
    "WARN_INK": "#8A4B00",
    "ERR_INK":  "#B91C1C",

    # 측정면 — 종이. 카드·표·캔버스.
    "PAPER":  "#FFFFFF",
    "CANVAS": "#EDEFF2",
    "FIELD":  "#F5F6F8",
    "RULE":   "#DCE0E6",
    "RULE_2": "#C6CCD4",        # 입력 테두리 (더 진하게)
    "TEXT":   "#12161B",
    "MUTED":  "#5C6672",        # PAPER 대비 5.9:1 (예전 #a1a1a6은 2.3:1)

    # 신호 — 주 동작·상태. 액센트는 차분한 보라 — 딥 인디고·파랑은 Okabe-Ito
    # 파랑(#0072B2)·타깃 파랑(#0071e3)과 겹치기 쉽고 레거시 공정관리 툴
    # 냄새가 난다 (`design-plans/calm-accent-violet.md`).
    "ACC":       "#6D5BD0",     # 보라. 흰 글자 5.18:1
    "ACC_H":     "#5A49B8",
    "ACC_SOFT":  "#F2F0FC",
    "ACC_LINE":  "#B9B0E8",
    "WARN":      "#8A4B00",     # 미적용(dirty). 흰 글자 6.8:1
    "WARN_H":    "#6F3C00",
    "WARN_SOFT": "#FEF3E2",
    "WARN_TEXT": "#7A4300",     # WARN_SOFT 위 글자
    "ERR":       "#B91C1C",     # 흰 글자 6.5:1
    "ERR_SOFT":  "#FDECEC",
    "OK":        "#136B3A",     # 흰 글자 6.6:1
    "OK_SOFT":   "#E7F3EB",
    "DIM":       "#7C8593",     # 흐린 글자·플레이스홀더 (FIELD 위 3.4:1)
    "DISABLED":  "#CBD1D9",     # 비활성 버튼 표면 (그 위 글자는 MUTED)

    # 형태 — 바깥(카드)일수록 크게, 안쪽(뱃지·칩)일수록 작게. 전부 같은
    # 모서리를 주면 카드 안의 요소가 카드처럼 보여 층이 사라진다.
    "RADIUS":    "8px",
    "RADIUS_LG": "14px",
    "RADIUS_SM": "4px",

    # 글자 크기 — 6단계만 쓴다
    "FS_MICRO": "11px",
    "FS_SM":    "11.5px",
    "FS_BASE":  "13px",
    "FS_NUM":   "12.5px",       # 모노(숫자·식별자)
    "FS_LG":    "15px",
    "FS_XL":    "20px",
    "FS_XXL":   "26px",
}

# QSS 안에서 텍스트/배경으로 쓰이는 짝 — test_theme.py가 AA(4.5:1)를 검사한다.
CONTRAST_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("본문",              "TEXT",      "PAPER"),
    ("본문(캔버스)",      "TEXT",      "CANVAS"),
    ("보조 문구",         "MUTED",     "PAPER"),
    ("보조 문구(입력면)", "MUTED",     "FIELD"),
    ("크롬 본문",         "INK_TEXT",  "INK"),
    ("크롬 보조",         "INK_MUTED", "INK"),
    ("주 버튼",           "PAPER",     "ACC"),
    ("주 버튼 hover",     "PAPER",     "ACC_H"),
    ("미적용 버튼",       "PAPER",     "WARN"),
    ("미적용 버튼 hover", "PAPER",     "WARN_H"),
    ("경고 문구",         "WARN_TEXT", "WARN_SOFT"),
    ("오류 문구",         "ERR",       "ERR_SOFT"),
    ("완료 문구",         "OK",        "OK_SOFT"),
    ("선택 항목",         "ACC",       "ACC_SOFT"),
    ("크롬 액센트",       "ACC_INK",   "INK"),
    ("크롬 완료",         "OK_INK",    "INK"),
    ("크롬 미적용",       "WARN_INK",  "INK"),
    ("크롬 오류",         "ERR_INK",   "INK"),
    ("크롬 본문(입력면)", "INK_TEXT",  "INK_2"),
)


def qss_path() -> Path:
    """style.qss의 실제 자리. **exe에서는 모듈 옆이 아니다** — resources가 찾는다."""
    from etreport import resources
    return resources.path("ui/style.qss")


# ── 아이콘 ────────────────────────────────────────────────────────────────
# 콤보 화살표·체크 표시는 QSS로 그릴 수 없어 이미지가 필요하다. 파일로 들고
# 다니면 그 안에 색이 복제되고(토큰이 두 곳에 생긴다) PyInstaller에도 따로
# 실어야 하므로, **토큰에서 만들어 캐시 폴더에 쓴다**. 실패하면 아이콘 없이
# 진행한다 — Fusion이 기본 화살표를 그려 준다.
_ICONS = {
    "chevron":     ('<path d="M2 4l4 4 4-4" fill="none" stroke="{MUTED}" '
                    'stroke-width="1.6" stroke-linecap="round" '
                    'stroke-linejoin="round"/>'),
    "chevron-dim": ('<path d="M2 4l4 4 4-4" fill="none" stroke="{DIM}" '
                    'stroke-width="1.6" stroke-linecap="round" '
                    'stroke-linejoin="round"/>'),
    "chevron-ink": ('<path d="M2 4l4 4 4-4" fill="none" stroke="{INK_MUTED}" '
                    'stroke-width="1.6" stroke-linecap="round" '
                    'stroke-linejoin="round"/>'),
    "check":       ('<path d="M2.5 6.2l2.6 2.6L9.6 3.4" fill="none" '
                    'stroke="{PAPER}" stroke-width="1.8" stroke-linecap="round" '
                    'stroke-linejoin="round"/>'),
    "dash":        ('<path d="M3 6h6" fill="none" stroke="{PAPER}" '
                    'stroke-width="1.8" stroke-linecap="round"/>'),
}
_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" '
        'viewBox="0 0 12 12">{body}</svg>')


def icon_dir() -> Path | None:
    """토큰 색으로 만든 아이콘 폴더. 쓰지 못하면 None.

    SVG 이미지 플러그인이 없는 배포본(PyInstaller에서 qsvg가 빠지는 경우)에서는
    아이콘을 걸어도 **빈 화살표**가 되므로, 아예 걸지 않고 Fusion 기본 화살표에
    맡긴다 — 없는 것보다 낫다.
    """
    from PySide6.QtGui import QImageReader

    from etreport.paths import appdata_dir

    if b"svg" not in QImageReader.supportedImageFormats():
        log.warning("SVG 이미지 플러그인이 없어 기본 화살표를 씁니다")
        return None
    try:
        d = appdata_dir() / "icons"
        d.mkdir(exist_ok=True)
        for name, body in _ICONS.items():
            svg = _SVG.format(body=body.format(**TOKENS))
            f = d / f"{name}.svg"
            if not f.exists() or f.read_text(encoding="utf-8") != svg:
                f.write_text(svg, encoding="utf-8")
        return d
    except OSError as e:
        log.warning("아이콘을 만들지 못했습니다(%s) — 기본 화살표를 씁니다", e)
        return None


def render_qss(ui_font: str, mono_font: str, icons: Path | None = None) -> str:
    """style.qss의 %TOKEN% 자리를 채워 완성된 스타일시트를 돌려준다."""
    css = qss_path().read_text(encoding="utf-8")
    if icons is None:
        # 아이콘이 없으면 image 규칙만 통째로 지운다(줄 단위로 안전하게).
        css = "\n".join(ln for ln in css.splitlines() if "%ICONS%" not in ln)
    subs = dict(TOKENS, UI_FONT=ui_font, MONO_FONT=mono_font,
                ICONS=icons.as_posix() if icons else "")
    for key, val in subs.items():
        css = css.replace(f"%{key}%", val)
    return css


def apply(app) -> None:
    """Fusion + 팔레트 + 고정폭 숫자 + 스타일시트. 부팅에서 한 번 부른다."""
    from etreport import fonts

    fonts.setup_qt(app)                       # 한글 폰트 먼저 (OS별로 다르다)
    _use_fusion(app)
    _apply_palette(app)
    _tabular_numerals(app)

    if not qss_path().exists():               # 빌드 시 --add-data 누락 등
        log.warning("style.qss를 찾지 못했습니다: %s", qss_path())
        return
    ui_font, mono_font = fonts.qss_stacks()
    app.setStyleSheet(render_qss(ui_font, mono_font, icon_dir()))


def _use_fusion(app) -> None:
    from PySide6.QtWidgets import QStyleFactory

    style = QStyleFactory.create("Fusion")
    if style is None:                          # 있을 수 없지만 부팅을 막지는 않는다
        log.warning("Fusion 스타일을 만들지 못했습니다 — 기본 스타일로 진행")
        return
    app.setStyle(style)
    # 스타일시트를 걸면 Qt가 실제 스타일을 QStyleSheetStyle로 감싸 버려서
    # 나중에는 어떤 스타일 위에 얹혔는지 알 수 없다 — 여기서 남겨 둔다.
    app.setProperty("etreport_base_style", style.name())


def _apply_palette(app) -> None:
    """QSS가 닿지 않는 자리(툴팁·달력 팝업·비활성 글자)를 토큰에 맞춘다."""
    from PySide6.QtGui import QColor, QPalette

    C = QColor
    p = QPalette()
    roles = {
        QPalette.Window:          C(TOKENS["CANVAS"]),
        QPalette.WindowText:      C(TOKENS["TEXT"]),
        QPalette.Base:            C(TOKENS["PAPER"]),
        QPalette.AlternateBase:   C(TOKENS["FIELD"]),
        QPalette.Text:            C(TOKENS["TEXT"]),
        QPalette.PlaceholderText: C(TOKENS["DIM"]),
        QPalette.Button:          C(TOKENS["PAPER"]),
        QPalette.ButtonText:      C(TOKENS["TEXT"]),
        QPalette.BrightText:      C(TOKENS["ERR"]),
        QPalette.Highlight:       C(TOKENS["ACC"]),
        QPalette.HighlightedText: C(TOKENS["PAPER"]),
        QPalette.ToolTipBase:     C(TOKENS["PAPER"]),
        QPalette.ToolTipText:     C(TOKENS["TEXT"]),
        QPalette.Link:            C(TOKENS["ACC"]),
        QPalette.Mid:             C(TOKENS["RULE"]),
        QPalette.Midlight:        C(TOKENS["FIELD"]),
        QPalette.Dark:            C(TOKENS["MUTED"]),
        QPalette.Shadow:          C(TOKENS["RULE_2"]),
    }
    for role, color in roles.items():
        p.setColor(role, color)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, C(TOKENS["DIM"]))
    app.setPalette(p)


def _tabular_numerals(app) -> None:
    """숫자를 고정폭으로 — 표·상태 레일에서 자릿수가 세로로 맞는다.

    Qt 6.7+의 폰트 피처 API. 폰트가 tnum을 갖고 있지 않으면 조용히 무시된다.
    """
    from PySide6.QtGui import QFont

    try:
        f = app.font()
        f.setFeature(QFont.Tag("tnum"), 1)
        app.setFont(f)
    except (AttributeError, TypeError):        # 구버전 Qt — 없어도 그만
        log.debug("고정폭 숫자 피처를 켜지 못했습니다", exc_info=True)
