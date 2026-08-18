# -*- mode: python ; coding: utf-8 -*-
"""ET Report — PyInstaller 빌드 정의. 산출물은 **단일 exe** 하나다.

    myenv/bin/python -m PyInstaller build/ETReport.spec --noconfirm --clean

빌드는 `build/build_release.py`가 이 파일을 불러 돌린다(버전 스탬프를 먼저 굽고
`--distpath/--workpath`를 지정하기 위해서). 이 spec만 직접 돌려도 만들어진다.

────────────────────────────────────────────────────────────────────────────
사내에서 고칠 자리
────────────────────────────────────────────────────────────────────────────
인증서·사내 라이브러리처럼 **여기 저장소에는 둘 수 없는 것**들이 있다. 그것을
spec 본문에 섞어 적으면 다음에 이 파일을 갱신할 때 통째로 날아간다. 그래서
갈아 끼우는 자리를 아래 세 리스트로 분리해 뒀다 — `SITE_*`만 채우면 된다.

  SITE_DATAS        (원본, 번들 안 폴더) 자료 파일. 예: 사내 CA 인증서, 설정
  SITE_BINARIES     (원본, 번들 안 폴더) .dll/.pyd 같은 바이너리
  SITE_HIDDEN       import를 정적으로 못 찾는 모듈 이름

저장소에 커밋하고 싶지 않으면 **`build/site_extras.py`** 를 만들어 같은 이름의
변수를 담아 두면 된다(있으면 자동으로 합쳐지고, `.gitignore`에 들어 있다).

    # build/site_extras.py  ← 커밋되지 않는다
    SITE_DATAS = [(r"C:\\certs\\company-ca.pem", "certs")]
    SITE_BINARIES = [(r"C:\\lib\\bdq_native.dll", ".")]
    SITE_HIDDEN = ["bigdataquery", "impala"]

번들 안에 실은 파일은 실행할 때 `sys._MEIPASS` 아래에 풀린다. 코드에서 찾을
때는 반드시 `etreport.resources`를 거친다 — `__file__` 기준으로 찾으면 exe에서
없는 경로가 나온다(그래서 style.qss를 못 찾는 경고가 났다).
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# spec 파일에는 __file__이 없다 — PyInstaller가 SPECPATH를 넣어 준다.
ROOT = Path(SPECPATH).resolve().parent          # noqa: F821
SRC = ROOT / "src"
PKG = SRC / "etreport"

# ── 사내 전용 추가분 (여기만 고친다) ─────────────────────────────────────
SITE_DATAS: list[tuple[str, str]] = []
SITE_BINARIES: list[tuple[str, str]] = []
SITE_HIDDEN: list[str] = []

_extras = ROOT / "build" / "site_extras.py"
if _extras.exists():                            # 커밋하지 않는 사내 설정
    _ns: dict = {}
    exec(compile(_extras.read_text(encoding="utf-8"), str(_extras), "exec"), _ns)
    SITE_DATAS += list(_ns.get("SITE_DATAS", []))
    SITE_BINARIES += list(_ns.get("SITE_BINARIES", []))
    SITE_HIDDEN += list(_ns.get("SITE_HIDDEN", []))
    print(f"[spec] site_extras.py 반영 — datas {len(_ns.get('SITE_DATAS', []))} "
          f"binaries {len(_ns.get('SITE_BINARIES', []))} "
          f"hidden {len(_ns.get('SITE_HIDDEN', []))}")

# ── 함께 실을 자료 파일 ──────────────────────────────────────────────────
# 번들 안 경로는 **`etreport/…`로 맞춘다** — resources.candidates()가 먼저 보는 자리다.
datas: list[tuple[str, str]] = [(str(PKG / "ui" / "style.qss"), "etreport/ui")]
for rel in ("assets/manual", "assets/fonts"):
    d = PKG / Path(rel)
    if d.is_dir() and any(d.iterdir()):         # 없으면 그냥 뺀다(설명서·폰트는 선택)
        datas.append((str(d), f"etreport/{rel}"))
stamp = PKG / "assets" / "build_info.json"      # build_release.py가 굽는다
if stamp.exists():
    datas.append((str(stamp), "etreport/assets"))
datas += SITE_DATAS

binaries: list[tuple[str, str]] = list(SITE_BINARIES)

# ── import 그래프가 놓치는 것들 ──────────────────────────────────────────
hiddenimports = collect_submodules("etreport") + [
    # 백엔드·드라이버는 문자열로 골라 쓰므로 정적 분석에 안 걸린다
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_agg",
    "duckdb",
    "pyarrow",
    "pyarrow.parquet",
    "polars",
    "pptx",
    "requests",
    "packaging.version",
] + SITE_HIDDEN

# PySide6는 플러그인(플랫폼·이미지 포맷)이 별도 파일이라 통째로 걷어야 한다.
# **SVG 이미지 플러그인이 빠지면 콤보 화살표가 빈 사각형이 된다**(theme.icon_dir).
for pkg in ("PySide6", "shiboken6"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

excludes = [
    "tkinter", "test", "unittest", "pydoc_data",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
    "PySide6.QtQuick3D", "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
]


a = Analysis(                                   # noqa: F821
    [str(PKG / "__main__.py")],
    # **소스 트리를 맨 앞에 둔다.** 이게 없으면 site-packages에 예전 etreport가
    # 설치돼 있을 때 그쪽이 번들에 들어가고, 소스를 고쳐도 exe가 그대로다.
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)                               # noqa: F821

exe = EXE(                                      # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ETReport",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX는 사내 백신이 자주 오탐한다 — 압축하지 않는다
    runtime_tmpdir=None,
    # 콘솔 없음(--windowed). sys.stderr가 None이 되므로 로깅은 app.py가
    # 파일 핸들러로만 붙이고, 처리되지 않은 예외는 excepthook이 창으로 돌린다.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PKG / "assets" / "app.ico")
    if (PKG / "assets" / "app.ico").exists() else None,
)
