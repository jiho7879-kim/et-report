"""사용자별 저장 위치와 그 안의 파일을 다루는 규칙. 전부 로컬(공유 드라이브 없음).

JSON 저장은 `write_json_atomic()`을 쓴다 — 설정·제외 목록·카탈로그는 앱이
죽는 순간에도 반쯤 쓰인 파일이 남으면 안 된다(특히 settings.json은 종료 시점에
저장되므로, 깨지면 프리셋이 전부 사라진다).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

STAGING_KEEP_DAYS = 7          # 추출 원본 parquet 보관 기간


def appdata_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / "ETReport"
    p.mkdir(parents=True, exist_ok=True)
    return p

def settings_file() -> Path:      return appdata_dir() / "settings.json"
def catalog_cache_file() -> Path: return appdata_dir() / "column_catalog.json"
def sessions_dir() -> Path:
    p = appdata_dir() / "sessions"; p.mkdir(exist_ok=True); return p
def staging_dir() -> Path:
    p = appdata_dir() / "staging"; p.mkdir(exist_ok=True); return p
def update_tmp_dir() -> Path:
    p = appdata_dir() / "update_tmp"; p.mkdir(exist_ok=True); return p
def log_dir() -> Path:
    p = appdata_dir() / "logs"; p.mkdir(exist_ok=True); return p
def log_file() -> Path:           return log_dir() / "etreport.log"


def write_json_atomic(path: Path, data, **dumps_kw) -> None:
    """같은 폴더의 임시 파일에 쓰고 os.replace로 교체 — 반쯤 쓰인 파일이 남지 않는다.

    os.replace는 같은 볼륨 안에서 원자적이다(Windows 포함). 실패하면 임시 파일을
    치우고 예외를 그대로 올린다 — 원본은 손대지 않은 상태로 남는다.
    """
    dumps_kw.setdefault("ensure_ascii", False)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    try:
        tmp.write_text(json.dumps(data, **dumps_kw), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def cleanup_staging(keep_days: int = STAGING_KEEP_DAYS) -> int:
    """staging의 오래된 parquet 삭제. 지운 개수를 반환.

    추출 원본은 리포메터를 고쳐 다시 돌릴 때 재사용하므로 바로 지우지 않고
    keep_days 동안 남긴다. 그냥 두면 하루 수십 MB씩 무한정 쌓인다.
    """
    cutoff = time.time() - keep_days * 86400
    gone = 0
    for f in staging_dir().glob("*.parquet"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                gone += 1
        except OSError as e:
            log.debug("staging 정리 건너뜀 %s: %s", f.name, e)
    return gone
