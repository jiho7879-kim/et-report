"""제외 포인트 저장소 — 원본 DuckDB를 절대 건드리지 않는다.

분석 화면은 DB를 **읽기 전용**으로 연다(사용자 요청: 조회용). 따라서 제외
목록은 DB가 아니라 %APPDATA%\\ETReport\\exclusions\\<해시>.json 에 둔다.
DB 파일 경로별로 따로 저장되므로 DB를 바꿔도 섞이지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from etreport.paths import appdata_dir, write_json_atomic

log = logging.getLogger(__name__)


def _dir() -> Path:
    p = appdata_dir() / "exclusions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file(db_path: str, kind: str = "") -> Path:
    """DB 경로별 사이드카 파일.

    `kind`는 손으로 찍은 제외("")와 이상치 필터가 걸러 낸 점("filter")을 **다른
    파일로** 나누기 위한 것이다. 한 파일에 섞으면 필터를 끌 때 사람이 찍은 제외까지
    함께 지워야 하고, 되돌릴 방법이 없다.
    """
    key = hashlib.blake2b(str(Path(db_path).resolve()).lower().encode(),
                          digest_size=8).hexdigest()
    suffix = f".{kind}" if kind else ""
    return _dir() / f"{Path(db_path).stem}_{key}{suffix}.json"


def load(db_path: str, kind: str = "") -> dict[str, dict]:
    """key → {reason, at}."""
    f = _file(db_path, kind)
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        return raw.get("points", {})
    except (json.JSONDecodeError, OSError) as e:
        log.warning("제외 목록 읽기 실패(%s) — 빈 목록으로 시작", e)
        return {}


def save(db_path: str, points: dict[str, dict], kind: str = "") -> None:
    # 점을 찍을 때마다 호출된다 — 도중에 끊겨도 이전 목록이 살아 있도록 원자적으로.
    try:
        write_json_atomic(
            _file(db_path, kind),
            {"db": str(Path(db_path).resolve()), "points": points},
            indent=1)
    except OSError as e:
        log.warning("제외 목록 저장 실패: %s", e)


#: 이상치 필터가 걸러 낸 점의 사이드카 종류. 손으로 찍은 제외와 섞지 않는다.
FILTER_KIND = "filter"


def load_filtered(db_path: str) -> dict[str, dict]:
    """이상치 필터가 걸러 낸 점 — key → {reason, item, at}."""
    return load(db_path, FILTER_KIND)


def save_filtered(db_path: str, points: dict[str, dict]) -> None:
    save(db_path, points, FILTER_KIND)


def add(db_path: str, points: dict[str, dict], key: str,
        reason: str = "") -> None:
    points[key] = {"reason": reason,
                   "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    save(db_path, points)


def remove(db_path: str, points: dict[str, dict], key: str) -> None:
    points.pop(key, None)
    save(db_path, points)
