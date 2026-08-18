"""패키지와 함께 배포되는 파일(스타일시트·설명서·폰트)을 찾는 한 곳.

**`Path(__file__).with_name(...)`으로 리소스를 찾으면 exe에서 깨진다.** PyInstaller는
파이썬 모듈을 압축 아카이브(PYZ)에 넣기 때문에 `etreport.ui.theme.__file__`은 실제로
존재하지 않는 경로를 가리킨다. 반면 `--add-data`로 실은 파일은 실행할 때 풀리는
폴더(`sys._MEIPASS`) 아래에 놓인다. 그래서 둘은 같은 자리가 아니고, 소스 실행에서만
우연히 같아 보였다 — 현장에서 "style.qss를 찾지 못했습니다"가 뜬 이유가 이것이다.

찾는 순서는 **frozen 번들 → 소스 트리**다. 없으면 `None`을 돌려주고, 부르는 쪽이
없어도 되는 리소스인지(폰트·설명서) 없으면 안 되는지(스타일시트) 판단한다.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

#: 패키지 안에서의 상대 경로 → 실제 파일. 한 번 찾으면 기억한다(부팅에서 여러 번 부른다).
_cache: dict[str, Path | None] = {}


def bundle_dir() -> Path | None:
    """PyInstaller가 데이터를 풀어 둔 폴더. frozen이 아니면 None.

    onefile은 실행할 때마다 임시 폴더에 풀고, onedir은 `_internal`이 그 자리다 —
    둘 다 `sys._MEIPASS`로 통일돼 있어 빌드 방식을 구분할 필요가 없다.
    """
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def source_dir() -> Path:
    """소스 트리의 `src/etreport` — 개발 실행·테스트에서 쓰는 자리."""
    return Path(__file__).resolve().parent


def candidates(rel: str) -> list[Path]:
    """`rel`(예: `ui/style.qss`)을 찾아볼 자리들. 앞쪽이 우선.

    번들 안에서는 `etreport/ui/style.qss`(패키지 경로를 살려 실은 경우)와
    `ui/style.qss`(평평하게 실은 경우) 둘 다 본다 — spec을 손대다 한쪽만
    맞춰 놓고 나중에 반대로 바꿔도 조용히 깨지지 않게 한다.
    """
    out: list[Path] = []
    if (b := bundle_dir()) is not None:
        out += [b / "etreport" / rel, b / rel]
    out.append(source_dir() / rel)
    return out


def find(rel: str) -> Path | None:
    """리소스 파일의 실제 경로. 어디에도 없으면 None."""
    if rel in _cache:
        return _cache[rel]
    hit = next((p for p in candidates(rel) if p.exists()), None)
    if hit is None:
        log.warning("리소스를 찾지 못했습니다: %s (찾아본 곳: %s)", rel,
                    ", ".join(str(p) for p in candidates(rel)))
    _cache[rel] = hit
    return hit


def path(rel: str) -> Path:
    """`find`와 같되 없으면 **소스 트리 경로**를 돌려준다.

    "존재하지 않는 경로"라도 손에 쥐어 줘야 하는 자리가 있다 — 오류 메시지에
    적거나, 만들어 낼 파일의 자리를 정할 때. 존재 여부는 부르는 쪽이 확인한다.
    """
    return find(rel) or (source_dir() / rel)


def clear_cache() -> None:
    """테스트가 리소스를 옮겨 놓고 다시 찾게 할 때."""
    _cache.clear()
