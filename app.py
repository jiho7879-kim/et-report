#!/usr/bin/env python3
"""개발 실행 진입점 —  python app.py [--demo] [--no-update] [--log-level DEBUG]

사양서 §1 "개발 실행: `python app.py` (설치 없이)" 를 위한 스크립트다. 설치 없이
바로 뜨도록 src/ 를 경로에 얹고 `etreport.app:main()`을 부른다.

같은 main()을 부르는 경로가 셋 있으니 여기에 로직을 두지 말 것:
  - `python app.py`            ← 이 파일 (개발)
  - `etreport` 콘솔 스크립트    ← pip install -e . (pyproject [project.scripts])
  - 배포 exe                    ← build/build_release.py가 src/etreport/__main__.py를 얼린다
"""
from __future__ import annotations

import sys
from pathlib import Path

MIN_PYTHON = (3, 11)          # 사양서 §1 — polars·PySide6·DuckDB 조합의 하한


def _bootstrap() -> None:
    """실행 전 확인: 파이썬 버전 → src/ 경로."""
    if sys.version_info < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        have = ".".join(map(str, sys.version_info[:3]))
        raise SystemExit(
            f"Python {need} 이상이 필요합니다 (현재 {have}: {sys.executable})\n"
            f"프로젝트 venv로 실행하세요:  myenv/bin/python app.py")

    src = Path(__file__).resolve().parent / "src"
    if not src.is_dir():
        raise SystemExit(f"src/ 폴더를 찾지 못했습니다: {src}\n"
                         "저장소 루트에서 실행하세요.")
    if str(src) not in sys.path:              # 이미 설치돼 있어도 이 소스가 우선
        sys.path.insert(0, str(src))


def _main() -> int:
    _bootstrap()
    try:
        from etreport.app import main
    except ImportError as e:                  # 의존성 미설치를 트레이스백 대신 안내로
        raise SystemExit(
            f"모듈을 불러오지 못했습니다: {e}\n"
            "의존성을 설치하세요:  pip install -e \".[dev]\"") from e
    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
