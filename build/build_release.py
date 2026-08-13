"""PyInstaller onedir 빌드 → zip → 사내 GitHub 릴리스 업로드.

사용:  python build/build_release.py            # 빌드+zip만
       python build/build_release.py --publish  # 릴리스 생성·업로드까지

버전은 src/etreport/__init__.py 의 __version__ 하나만 고친다.
태그 vX.Y.Z 와 zip 이름은 여기서 자동으로 맞춘다 (checker.py 규칙과 동일).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from etreport import __version__

APP = "ETReport"
DIST = ROOT / "dist" / APP
ZIP = ROOT / "dist" / f"{APP}-{__version__}-win64.zip"


def build() -> None:
    sep = os.pathsep          # PyInstaller --add-data 구분자: Windows ';' / 그 외 ':'
    data = [f"{ROOT/'src'/'etreport'/'ui'/'style.qss'}{sep}etreport/ui"]
    manual = ROOT / "src" / "etreport" / "assets" / "manual"
    if manual.is_dir() and any(manual.iterdir()):       # 사용 설명서 PDF
        data.append(f"{manual}{sep}etreport/assets/manual")
    ko_fonts = ROOT / "src" / "etreport" / "assets" / "fonts"
    if ko_fonts.is_dir() and any(ko_fonts.iterdir()):   # 있으면 한글 폰트도 동봉
        data.append(f"{ko_fonts}{sep}etreport/assets/fonts")

    args = [sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", APP,
            "--collect-all", "PySide6",
            "--collect-submodules", "etreport"]
    for d in data:
        args += ["--add-data", d]
    args.append(str(ROOT / "src" / "etreport" / "__main__.py"))
    subprocess.check_call(args, cwd=ROOT)


def make_zip() -> None:
    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for p in DIST.rglob("*"):
            z.write(p, Path(APP) / p.relative_to(DIST))
    print(f"zip: {ZIP}  ({ZIP.stat().st_size/1e6:.1f} MB)")


def publish() -> None:
    """gh CLI 사용 (사내 GHE: gh auth login --hostname github.company.com)."""
    tag = f"v{__version__}"
    subprocess.check_call([
        "gh", "release", "create", tag, str(ZIP),
        "--title", f"{APP} {tag}",
        "--notes-file", str(ROOT / "CHANGELOG.md"),
        "--repo", "pde-tools/et-report",
    ])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    build()
    make_zip()
    if a.publish:
        publish()
