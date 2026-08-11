"""사용자별 저장 위치. 공유 드라이브를 쓰지 않으므로 전부 로컬."""
from __future__ import annotations

import os
from pathlib import Path


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
