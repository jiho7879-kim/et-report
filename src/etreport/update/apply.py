"""다운로드한 zip을 앱 종료 후 설치 폴더에 덮어쓰고 재시작.

Windows에서 실행 중인 exe/dll은 잠겨 있으므로, 본 프로세스가 종료된 뒤
교체를 수행할 배치 스크립트를 떨궈 놓고 detach 실행한다.
PyInstaller --onedir 배포를 전제로 한다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

from etreport.paths import update_tmp_dir

_BAT = r"""@echo off
rem ─ ET Report 자동 업데이트 ─
rem  %1 pid   %2 새 버전 폴더   %3 설치 폴더   %4 재시작 exe
setlocal
:wait
tasklist /FI "PID eq %~1" 2>nul | find "%~1" >nul
if not errorlevel 1 ( timeout /t 1 /nobreak >nul & goto wait )

robocopy "%~2" "%~3" /MIR /R:3 /W:1 >nul
if errorlevel 8 (
  msg %username% "ET Report 업데이트 실패 — 파일 복사 오류. IT 담당자에게 문의하세요." 
  exit /b 1
)
start "" "%~4"
rem 자기 자신과 임시 폴더 정리
rmdir /s /q "%~2"
del "%~f0"
"""


def install_dir() -> Path:
    """PyInstaller onedir의 설치 루트(= exe가 있는 폴더)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # 개발 실행 시엔 교체 대상이 없다 — 테스트용 더미
    return Path(__file__).resolve().parents[3]


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
        z.extractall(out)
    entries = list(out.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return out


def apply_and_restart(new_dir: Path) -> None:
    """배치를 떨궈 detach 실행한 뒤 호출측(QApplication)이 종료하면 교체된다."""
    target = install_dir()
    exe = target / (Path(sys.executable).name if getattr(sys, "frozen", False) else "ETReport.exe")
    bat = update_tmp_dir() / "apply_update.bat"
    bat.write_text(_BAT, encoding="cp949")   # cmd 콘솔 인코딩
    subprocess.Popen(
        ["cmd", "/c", str(bat), str(os.getpid()), str(new_dir), str(target), str(exe)],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
