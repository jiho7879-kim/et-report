#!/usr/bin/env python3
"""개발 실행 진입점 —  python app.py

배포는 PyInstaller로 exe를 만든다(build/build_release.py). 여기서는
설치 없이 바로 뜨도록 src/ 를 경로에 얹고 앱을 띄운다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from etreport.app import main

if __name__ == "__main__":
    raise SystemExit(main())
