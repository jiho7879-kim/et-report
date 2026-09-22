"""내려받은 자산을 앱 종료 후 설치 자리에 덮어쓰고 재시작.

Windows에서 **실행 중인 exe·dll은 잠겨 있다**. 그래서 본 프로세스가 끝나기를
기다렸다가 교체하는 배치 스크립트를 떨궈 놓고 detach로 띄운다.

배포 형태가 둘이라 교체 방법도 둘이다.
  - **단일 exe**(지금 기본) — 받은 .exe 하나를 현재 exe 자리에 덮어쓴다.
    옆 폴더에 흩어진 파일이 없으므로 교체가 파일 하나로 끝난다.
  - **폴더 배포**(예전 onedir zip) — zip을 풀어 설치 폴더에 robocopy로 부어넣는다.

어느 쪽인지는 받은 파일의 확장자로 정한다(`plan()`) — 실행 중인 형태가 아니라
**받은 자산**이 기준이다. 폴더 배포를 쓰다가 단일 exe 릴리스를 받는 전환도
이 규칙으로 자연스럽게 넘어간다.

교체 실패는 조용히 넘기지 않는다. robocopy는 8 이상이 진짜 실패이고, copy는
errorlevel이 0이 아니면 실패다 — 실패하면 예전 파일이 그대로 남고 사용자에게
알린다(반쯤 덮인 설치본이 가장 나쁘다).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from etreport.paths import update_tmp_dir

log = logging.getLogger(__name__)

#: 폴더 배포 교체 — %1 pid  %2 새 폴더  %3 설치 폴더  %4 재시작할 exe  %5 로그
_BAT_DIR = """@echo off
rem - ET Report auto update (folder) -
setlocal
echo [%DATE% %TIME%] start pid=%~1 src=%~2 dst=%~3 > "%~5"
:wait
tasklist /FI "PID eq %~1" 2>nul | find "%~1" >nul
if not errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto wait )

set /a TRY=0
:copy
set /a TRY+=1
robocopy "%~2" "%~3" /E /R:3 /W:1 >>"%~5"
if not errorlevel 8 goto done
if %TRY% LSS 20 ( ping -n 3 127.0.0.1 >nul & goto copy )
echo [%DATE% %TIME%] robocopy failed after %TRY% tries >> "%~5"
start "" "%~4"
exit /b 1

:done
echo [%DATE% %TIME%] copied ok >> "%~5"
start "" "%~4"
rem clean up the temp folder and this script
rmdir /s /q "%~2"
del "%~f0"
"""

#: 단일 exe 교체 — %1 pid  %2 새 exe(_temp)  %3 현재 exe  %4 로그
#:
#: `timeout`을 쓰지 않는다 — DETACHED_PROCESS로 띄운 배치에는 콘솔이 없어
#: `timeout`이 "Input redirection is not supported"로 **즉시** 실패한다. 기다리는
#: 척만 하고 안 기다리니 앱이 방금 놓은 exe 잠금이 풀리기 전에 copy가 돌아
#: 교체가 조용히 실패했다. `ping -n`은 콘솔 없이도 진짜로 쉰다.
_BAT_EXE = """@echo off
rem - ET Report auto update (single exe) -
setlocal
echo [%DATE% %TIME%] start pid=%~1 src=%~2 dst=%~3 > "%~4"
:wait
tasklist /FI "PID eq %~1" 2>nul | find "%~1" >nul
if not errorlevel 1 ( ping -n 2 127.0.0.1 >nul & goto wait )

rem  exe stays locked for a moment after the process exits - retry, do not give up fast
set /a TRY=0
:copy
set /a TRY+=1
copy /Y "%~2" "%~3" >>"%~4" 2>&1
if not errorlevel 1 goto done
if %TRY% LSS 20 ( ping -n 3 127.0.0.1 >nul & goto copy )
rem  give up: leave the new exe beside the old one so it can be renamed by hand
echo [%DATE% %TIME%] replace failed after %TRY% tries - kept "%~2" >> "%~4"
start "" "%~3"
exit /b 1

:done
echo [%DATE% %TIME%] replaced ok >> "%~4"
start "" "%~3"
del /q "%~2"
del "%~f0"
"""


class UpdateNotApplicable(RuntimeError):
    """교체할 설치 자리가 없는 실행 형태 — 적용을 시도하면 안 된다."""


def is_onefile() -> bool:
    """단일 exe로 실행 중인가.

    onefile은 데이터를 임시 폴더에 풀기 때문에 `sys._MEIPASS`가 exe 옆이 아니다.
    onedir은 `_internal`이 exe 옆에 있다 — 그 차이로 구분한다.
    """
    if not getattr(sys, "frozen", False):
        return False
    from etreport import resources
    b = resources.bundle_dir()
    return not (b and b.parent == Path(sys.executable).parent)


def install_dir() -> Path:
    """설치 루트(= exe가 있는 폴더).

    **개발 실행(소스에서 python app.py)에서는 교체 대상이 없다.** 예전에는
    저장소 루트를 돌려줬는데, 그 경로가 robocopy의 대상이 되어 [지금 업데이트]
    한 번에 소스 트리가 덮여 사라질 수 있었다. 이제는 예외를 던져 막는다.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    raise UpdateNotApplicable(
        "소스에서 실행 중입니다 — 자동 업데이트는 배포된 exe에서만 적용됩니다.\n"
        "개발 중이라면 git pull로 갱신하세요.")


def extract(zip_path: Path) -> Path:
    """zip을 임시 폴더에 풀고 새 버전 루트를 반환.

    zip 안이 단일 최상위 폴더(ETReport/…)든 평평하든 모두 처리.
    """
    out = update_tmp_dir() / "new"
    if out.exists():
        import shutil
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            # zipfile이 자체 정규화를 하긴 하지만, 릴리스 zip이 이상하면
            # 조용히 이름이 바뀌는 것보다 멈추는 편이 낫다.
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError(f"업데이트 zip에 비정상 경로가 있습니다: {name}")
        z.extractall(out)
    entries = list(out.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return out


def plan(asset: Path) -> tuple[str, Path]:
    """받은 자산 → (교체 방식, 교체에 쓸 경로).

    `("exe", 새 exe)` 또는 `("dir", 풀어 놓은 새 폴더)`. 확장자를 모르면 zip으로
    본다 — 예전 릴리스가 전부 zip이라 그쪽이 안전한 기본값이다.
    """
    if asset.suffix.lower() == ".exe":
        return "exe", asset
    return "dir", extract(asset)


def stage_beside(source: Path, cur: Path) -> Path:
    """새 exe를 현재 exe 옆에 `…_temp.exe`로 먼저 복사하고 그 경로를 반환.

    이유가 둘이다.
      1) **설치 폴더에 쓸 수 있는지 앱이 살아 있는 동안 확인한다.** Program Files
         처럼 권한이 없으면 예전에는 앱을 닫은 뒤 배치가 조용히 실패해서, 사용자
         입장에서는 "업데이트를 눌렀는데 그냥 예전 버전이 다시 떴다"로 끝났다.
      2) **교체가 실패해도 `…_temp.exe`가 exe 옆에 남는다.** 이름만 바꾸면 손으로
         올릴 수 있다 — `%APPDATA%` 안에만 있으면 현장에서 찾지 못한다.
    성공하면 배치가 지운다.
    """
    import shutil

    staged = cur.with_name(cur.stem + "_temp" + cur.suffix)
    try:
        shutil.copy2(source, staged)
    except OSError as e:
        raise UpdateNotApplicable(
            f"설치 폴더에 쓸 수 없어 업데이트를 적용할 수 없습니다:\n{staged}\n{e}\n"
            "관리자 권한으로 실행하거나 IT에 문의하세요.") from e
    return staged


def apply_and_restart(source: Path, kind: str = "") -> None:
    """배치를 떨궈 detach 실행 — 호출측(QApplication)이 종료하면 교체된다.

    `kind`를 주지 않으면 `source`가 파일이면 exe, 폴더면 dir로 본다.
    적용은 **배포된 exe에서만** 한다(소스 실행이면 install_dir이 막는다).
    """
    if sys.platform != "win32":
        raise UpdateNotApplicable("자동 업데이트는 Windows 배포본에서만 동작합니다")
    kind = kind or ("exe" if source.is_file() else "dir")
    target = install_dir()                    # 소스 실행이면 여기서 중단된다
    bat = update_tmp_dir() / "apply_update.bat"
    blog = update_tmp_dir() / "apply_update.log"

    if kind == "exe":
        cur = Path(sys.executable)
        staged = stage_beside(source, cur)
        # 배치는 ASCII만 쓴다 — 콘솔 코드페이지(한국어/영문 Windows)에 의존하지 않도록.
        bat.write_text(_BAT_EXE, encoding="ascii")
        argv = [str(os.getpid()), str(staged), str(cur), str(blog)]
        log.info("업데이트 적용(단일 exe): %s → %s", staged, cur)
    else:
        bat.write_text(_BAT_DIR, encoding="ascii")
        exe = target / Path(sys.executable).name
        argv = [str(os.getpid()), str(source), str(target), str(exe), str(blog)]
        log.info("업데이트 적용(폴더): %s → %s", source, target)

    # **현재 폴더를 물려주지 않는다.** 자식은 부모의 CWD를 그대로 받는데, 단일
    # exe는 그 자리가 `%TEMP%\_MEIxxxxx`일 수 있다 — 열려 있는 폴더는 지울 수
    # 없어서 종료할 때 "Failed to remove temporary directory"가 뜬다.
    subprocess.Popen(
        ["cmd", "/c", str(bat), *argv],
        cwd=str(update_tmp_dir()),
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
