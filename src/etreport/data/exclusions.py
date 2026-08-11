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

from etreport.paths import appdata_dir

log = logging.getLogger(__name__)


def _dir() -> Path:
    p = appdata_dir() / "exclusions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file(db_path: str) -> Path:
    key = hashlib.blake2b(str(Path(db_path).resolve()).lower().encode(),
                          digest_size=8).hexdigest()
    return _dir() / f"{Path(db_path).stem}_{key}.json"


def load(db_path: str) -> dict[str, dict]:
    """key → {reason, at}."""
    f = _file(db_path)
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        return raw.get("points", {})
    except (json.JSONDecodeError, OSError) as e:
        log.warning("제외 목록 읽기 실패(%s) — 빈 목록으로 시작", e)
        return {}


def save(db_path: str, points: dict[str, dict]) -> None:
    try:
        _file(db_path).write_text(json.dumps(
            {"db": str(Path(db_path).resolve()), "points": points},
            ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("제외 목록 저장 실패: %s", e)


def add(db_path: str, points: dict[str, dict], key: str,
        reason: str = "") -> None:
    points[key] = {"reason": reason,
                   "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    save(db_path, points)


def remove(db_path: str, points: dict[str, dict], key: str) -> None:
    points.pop(key, None)
    save(db_path, points)
