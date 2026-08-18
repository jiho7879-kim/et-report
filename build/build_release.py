"""PyInstaller **단일 exe** 빌드 → 사내 GitHub 릴리스 업로드.

사용:  python build/build_release.py                # dist/ETReport-<ver>.exe
       python build/build_release.py --publish      # 릴리스 생성·업로드까지
       python build/build_release.py --onedir       # 예전 폴더 배포(+zip)
       python build/build_release.py --no-clean     # 캐시 재사용(빠름, 권장하지 않음)

버전은 `src/etreport/__init__.py`의 `__version__` 하나만 고친다. 태그 `vX.Y.Z`와
자산 이름은 여기서 자동으로 맞춘다(`update/checker.py` 규칙과 동일).

**빌드 정의는 `build/ETReport.spec`에 있다** — 사내에서 인증서·라이브러리를 더
실어야 하면 그 파일(또는 커밋되지 않는 `build/site_extras.py`)만 고친다.

"코드를 고쳤는데 exe가 그대로"를 없애기 위해 세 가지를 한다.
  1. 빌드 전에 `assets/build_info.json`(시각·git 해시)을 굽고 번들에 싣는다 —
     실행 중인 exe가 어느 소스로 만들어졌는지 [도움말] → [정보]에서 보인다.
  2. spec의 `pathex`가 `src/`를 맨 앞에 두어 **소스 트리가 항상 이긴다**
     (site-packages에 설치된 예전 etreport가 대신 실리는 사고를 막는다).
  3. 작업 폴더를 `build/pyi`로 내려 저장소의 `build/`와 섞이지 않게 하고,
     기본으로 `--clean`을 준다 — 남아 있던 분석 캐시가 예전 모듈을 재사용하지
     못하게 한다.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from etreport import __version__

APP = "ETReport"
SPEC = ROOT / "build" / "ETReport.spec"
WORK = ROOT / "build" / "pyi"                 # PyInstaller 작업 폴더 (기본값은 ./build)
DIST = ROOT / "dist"
STAMP = ROOT / "src" / "etreport" / "assets" / "build_info.json"

EXE_OUT = DIST / f"{APP}-{__version__}-win64.exe"
DIR_OUT = DIST / APP
ZIP_OUT = DIST / f"{APP}-{__version__}-win64.zip"


def git_commit() -> str:
    """짧은 해시(+dirty). git이 없거나 저장소가 아니면 빈 문자열."""
    try:
        h = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                    cwd=ROOT, text=True,
                                    stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"],
                                        cwd=ROOT, text=True,
                                        stderr=subprocess.DEVNULL).strip()
        return h + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return ""


def write_stamp(mode: str) -> None:
    """빌드 스탬프를 소스 트리에 굽는다 — spec이 이걸 번들에 싣는다."""
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(json.dumps({
        "version": __version__,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "commit": git_commit(),
        "mode": mode,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[build] 스탬프 {STAMP.name}: {STAMP.read_text(encoding='utf-8')}")


def build(onedir: bool, clean: bool) -> Path:
    """PyInstaller 실행. 만들어진 산출물 경로를 돌려준다."""
    args = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm",
            "--distpath", str(DIST), "--workpath", str(WORK)]
    if clean:
        args.append("--clean")
    if onedir:
        # spec은 onefile이 기본이다. 폴더 배포는 예전 방식이라 옵션으로만 남긴다.
        args += ["--onedir"]
    subprocess.check_call(args, cwd=ROOT)

    made = DIST / (APP if onedir else f"{APP}.exe")
    if not made.exists():
        raise SystemExit(f"빌드 산출물이 없습니다: {made}")
    out = DIR_OUT if onedir else EXE_OUT
    if out != made:
        if out.exists():
            shutil.rmtree(out) if out.is_dir() else out.unlink()
        made.rename(out)
    return out


def make_zip() -> None:
    """onedir 배포용 zip (예전 업데이트 경로와의 호환)."""
    if ZIP_OUT.exists():
        ZIP_OUT.unlink()
    with zipfile.ZipFile(ZIP_OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in DIR_OUT.rglob("*"):
            z.write(p, Path(APP) / p.relative_to(DIR_OUT))
    print(f"zip: {ZIP_OUT}  ({ZIP_OUT.stat().st_size/1e6:.1f} MB)")


def publish(asset: Path) -> None:
    """gh CLI 사용 (사내 GHE: gh auth login --hostname github.company.com)."""
    tag = f"v{__version__}"
    subprocess.check_call([
        "gh", "release", "create", tag, str(asset),
        "--title", f"{APP} {tag}",
        "--notes-file", str(ROOT / "CHANGELOG.md"),
        "--repo", "pde-tools/et-report",
    ])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--onedir", action="store_true",
                    help="예전 폴더 배포 + zip (기본은 단일 exe)")
    ap.add_argument("--no-clean", dest="clean", action="store_false",
                    help="PyInstaller 캐시를 지우지 않는다(빠르지만 예전 모듈이 남을 수 있다)")
    a = ap.parse_args()

    shutil.rmtree(DIST, ignore_errors=True)
    write_stamp("onedir" if a.onedir else "onefile")
    out = build(a.onedir, a.clean)
    if a.onedir:
        make_zip()
        out = ZIP_OUT
    print(f"산출물: {out}  ({out.stat().st_size/1e6:.1f} MB)")
    if a.publish:
        publish(out)
