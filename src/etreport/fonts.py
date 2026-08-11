"""한글 폰트 해결 — Windows/macOS/Linux(WSL 포함) 어디서나 한글이 □로 깨지지 않게.

Qt(UI)와 matplotlib(PPT 이미지)이 서로 다른 폰트 목록을 보기 때문에, 후보
목록·탐색 규칙을 여기 한 곳에 두고 양쪽에서 같은 폰트를 고른다.

찾는 순서:
  1) 환경변수 ETREPORT_FONT (패밀리 이름 또는 폰트 파일 경로)
  2) 같이 배포한 폰트  src/etreport/assets/fonts/*.ttf|otf|ttc
  3) 시스템에 설치돼 이미 Qt/matplotlib가 아는 한글 패밀리 (Malgun Gothic 등)
  4) 설치는 됐지만 Qt/matplotlib가 모르는 폰트 파일 직접 등록
     (리눅스 폰트 디렉터리 + WSL의 /mnt/c/Windows/Fonts)
  5) 하나도 없으면 1회 경고 — 설치 방법을 로그로 안내한다.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# 선호 순. 앞쪽이 우선.
KOREAN_FAMILIES = [
    "Pretendard", "Malgun Gothic", "Apple SD Gothic Neo", "AppleGothic",
    "Noto Sans CJK KR", "Noto Sans KR", "NanumGothic", "Nanum Gothic",
    "NanumBarunGothic", "Source Han Sans KR", "UnDotum", "Gulim", "Batang",
]

# 파일로 직접 등록할 때 찾을 이름(대소문자 무시). ttf/otf를 ttc보다 먼저 본다.
_FILE_PATTERNS = [
    "pretendard*", "malgun*", "nanumgothic*", "nanumbarungothic*",
    "notosanskr*", "notosanscjk*kr*", "notosanscjk-regular*",
    "sourcehansans*", "applesdgothicneo*", "applegothic*",
    "undotum*", "gulim*", "batang*",
]
_FILE_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")

# QSS·matplotlib에 넘길 대체 목록(한글 폰트는 resolve 결과를 앞에 붙인다).
_UI_FALLBACK = ["Segoe UI", "Helvetica Neue", "Ubuntu", "DejaVu Sans", "sans-serif"]
_MONO_FALLBACK = ["Consolas", "D2Coding", "Menlo", "DejaVu Sans Mono",
                  "Ubuntu Mono", "Courier New", "monospace"]

_INSTALL_HINT = (
    "한글 폰트를 찾지 못했습니다 — 한글이 □로 보일 수 있습니다.\n"
    "  Ubuntu/Debian: sudo apt install -y fonts-noto-cjk fonts-nanum\n"
    "  RHEL/Fedora  : sudo dnf install -y google-noto-sans-cjk-fonts\n"
    "  또는 폰트 파일을 src/etreport/assets/fonts/ 에 넣거나 "
    "ETREPORT_FONT=<패밀리 또는 파일경로> 로 지정하세요."
)

_warned = False
_qt_family: str | None = None
_mpl_family: str | None = None
_files_cache: list[Path] | None = None


def _warn_missing() -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("%s", _INSTALL_HINT)


def bundled_dir() -> Path:
    """같이 배포한 폰트 폴더. PyInstaller onedir/onefile 모두에서 동작."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    p = base / "etreport" / "assets" / "fonts"
    return p if p.is_dir() else Path(__file__).resolve().parent / "assets" / "fonts"


def _search_dirs() -> list[Path]:
    home = Path.home()
    cands = [
        bundled_dir(),
        home / ".local" / "share" / "fonts", home / ".fonts",
        Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
        Path("/Library/Fonts"), home / "Library" / "Fonts",
        Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
        home / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
    ]
    # WSL — 윈도우에 깔린 폰트(맑은 고딕 등)를 그대로 쓴다.
    cands += [Path(f"/mnt/{d}/Windows/Fonts") for d in "cdef"]
    seen, out = set(), []
    for p in cands:
        try:
            if p.is_dir() and p not in seen:
                seen.add(p)
                out.append(p)
        except OSError:                      # 접근 불가한 마운트 등
            continue
    return out


def _candidate_files() -> list[Path]:
    """한글 폰트로 보이는 파일들 — 선호 순."""
    global _files_cache
    if _files_cache is not None:
        return _files_cache

    from fnmatch import fnmatch

    found: list[tuple[int, int, Path]] = []
    for d in _search_dirs():
        bundled = d == bundled_dir()
        try:
            entries = list(d.rglob("*"))
        except OSError:
            continue
        for f in entries:
            if f.suffix.lower() not in _FILE_SUFFIXES or not f.is_file():
                continue
            if bundled:                      # 배포 폰트는 이름 안 따지고 무조건 후보
                rank = -1
            else:
                low = f.name.lower()
                rank = next((i for i, pat in enumerate(_FILE_PATTERNS)
                             if fnmatch(low, pat)), None)
                if rank is None:
                    continue
            # ttc/otc는 matplotlib 지원이 약해 뒤로 민다
            found.append((rank, 1 if f.suffix.lower() in (".ttc", ".otc") else 0, f))
    found.sort(key=lambda t: (t[0], t[1], t[2].name))
    _files_cache = [f for _, _, f in found]
    return _files_cache


def _env_override() -> tuple[str | None, Path | None]:
    v = os.environ.get("ETREPORT_FONT", "").strip()
    if not v:
        return None, None
    p = Path(v).expanduser()
    if p.is_file():
        return None, p
    return v, None


def _pick(available: set[str]) -> str | None:
    fam, _ = _env_override()
    if fam and fam in available:
        return fam
    return next((f for f in KOREAN_FAMILIES if f in available), None)


# ── Qt ────────────────────────────────────────────────────────────────────
def setup_qt(app) -> str | None:
    """앱 기본 폰트를 한글 되는 폰트로 맞추고 패밀리 이름을 돌려준다."""
    global _qt_family
    if _qt_family is not None:
        return _qt_family

    from PySide6.QtGui import QFont, QFontDatabase

    fam, env_file = _env_override()
    added: list[str] = []
    if env_file:
        added += _add_qt_file(env_file)

    families = set(QFontDatabase.families())
    chosen = (added[0] if added else None) or _pick(families)

    if chosen is None:                        # 설치돼 있지만 Qt가 모르는 폰트 등록
        for f in _candidate_files():
            names = _add_qt_file(f)
            if names:
                chosen = names[0]
                log.info("한글 폰트 등록: %s (%s)", chosen, f)
                break

    if fam and chosen != fam:
        log.warning("ETREPORT_FONT=%s 를 찾지 못했습니다 — %s 사용", fam, chosen or "기본 폰트")
    if chosen is None:
        _warn_missing()
        return None

    _qt_family = chosen
    f = app.font()
    f.setFamily(chosen)
    f.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(f)
    return chosen


def _add_qt_file(path: Path) -> list[str]:
    from PySide6.QtGui import QFontDatabase
    fid = QFontDatabase.addApplicationFont(str(path))
    return [] if fid < 0 else list(QFontDatabase.applicationFontFamilies(fid))


def qss_stacks() -> tuple[str, str]:
    """style.qss 의 %UI_FONT% / %MONO_FONT% 자리에 넣을 문자열."""
    ko = _qt_family
    ui = ([ko] if ko else []) + KOREAN_FAMILIES + _UI_FALLBACK
    mono = _MONO_FALLBACK + ([ko] if ko else [])   # 코드 글꼴 뒤에 한글 보조
    return _quote(ui), _quote(mono)


def _quote(names: list[str]) -> str:
    return ",".join(f'"{n}"' for n in dict.fromkeys(names))


# ── matplotlib ────────────────────────────────────────────────────────────
def setup_matplotlib() -> str | None:
    """rcParams 를 한글 되는 폰트로 맞추고 패밀리 이름을 돌려준다."""
    global _mpl_family
    if _mpl_family is not None:
        return _mpl_family

    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    fam, env_file = _env_override()
    chosen = _add_mpl_file(env_file) if env_file else None
    chosen = chosen or _pick({f.name for f in font_manager.fontManager.ttflist})

    if chosen is None:
        for f in _candidate_files():
            chosen = _add_mpl_file(f)
            if chosen:
                log.info("한글 폰트 등록(matplotlib): %s (%s)", chosen, f)
                break
    if fam and chosen != fam:
        log.warning("ETREPORT_FONT=%s 를 찾지 못했습니다 — %s 사용", fam, chosen or "기본 폰트")

    # 같은 패밀리의 굵은 파일도 등록해 둔다 (bold 제목이 뭉개지지 않게)
    if chosen:
        for f in _candidate_files():
            _add_mpl_file(f, only_family=chosen)

    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.family"] = "sans-serif"
    order = ([chosen] if chosen else []) + KOREAN_FAMILIES + list(plt.rcParams["font.sans-serif"])
    plt.rcParams["font.sans-serif"] = list(dict.fromkeys(order))   # 순서 유지 중복 제거
    if chosen is None:
        _warn_missing()
    _mpl_family = chosen
    return chosen


def _add_mpl_file(path: Path, only_family: str | None = None) -> str | None:
    from matplotlib import font_manager
    try:
        name = font_manager.FontProperties(fname=str(path)).get_name()
        if only_family and name != only_family:
            return None
        font_manager.fontManager.addfont(str(path))
        return name
    except Exception:                        # noqa: BLE001 — 깨진 폰트 파일은 건너뛴다
        return None
