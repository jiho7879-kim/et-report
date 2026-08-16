#!/usr/bin/env python3
"""데모 번들 생성기 — 앱을 띄우지 않고 파일만 만든다.

`etreport --demo`가 부팅 때 하는 일과 같은 코드를 부른다. 시연용 폴더를 미리
만들어 두거나(사내 PC로 들고 가거나), DuckDB만 다시 만들고 싶을 때 쓴다.

사용 예
-------
    myenv/bin/python tools/make_demo.py                    # 기본 위치
    myenv/bin/python tools/make_demo.py --out /tmp/etdemo  # 위치 지정
    myenv/bin/python tools/make_demo.py --force            # 전부 다시 만들기

만들어지는 것은 `데모_안내.md`에 정리돼 있다.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from etreport import demo_bundle


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ET Report 데모 번들 생성기")
    ap.add_argument("--out", default="", help="저장할 폴더 (기본: APPDATA/demo)")
    ap.add_argument("--force", action="store_true", help="이미 있어도 다시 만든다")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    t0 = time.monotonic()
    b = demo_bundle.build(a.out or None, force=a.force)
    print(f"데모 번들: {b.root}  ({time.monotonic() - t0:.1f}초)")
    for p in sorted(b.root.iterdir()):
        if p.is_file():
            mark = "새로 만듦" if p.name in b.made else "그대로"
            print(f"  {p.name:<28} {p.stat().st_size / 1024:8.1f} KB  {mark}")
    print(f"\n앱에서 열기:  myenv/bin/python app.py --demo --demo-dir {b.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
