"""사내 GitHub(Enterprise) 릴리스에서 새 버전 확인.

배포 규칙
---------
- 릴리스 태그: ``vX.Y.Z``  (etreport.__version__ 과 동일하게)
- 자산(asset): ``ETReport-X.Y.Z-win64.exe``  — PyInstaller onefile 결과(지금 기본)
               ``ETReport-X.Y.Z-win64.zip``  — 예전 onedir 폴더를 통째로 zip

**exe를 먼저 고른다.** 둘 다 올라와 있으면 파일 하나만 바꾸면 되는 쪽이 안전하다.
실행 중인 exe는 Windows에서 잠겨 있으므로, 어느 쪽이든 앱이 끝난 뒤 배치가
교체한다(apply.py 참고).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import requests
from packaging.version import InvalidVersion, Version

from etreport import __version__

log = logging.getLogger(__name__)

# ── 사내 환경 설정 ────────────────────────────────────────────────
# GitHub Enterprise API 베이스. 예: https://github.company.com/api/v3
API_BASE = "https://github.company.com/api/v3"
OWNER = "pde-tools"
REPO = "et-report"
# 사내 저장소가 private이면 read 권한만 있는 토큰을 넣는다(없으면 None).
TOKEN: str | None = None
# 사내 CA 인증서 경로. requests 기본 번들에 사내 CA가 없으면 지정.
CA_BUNDLE: str | bool = True
TIMEOUT = 5  # 초 — 실패해도 앱 시작을 막지 않도록 짧게


@dataclass(frozen=True)
class UpdateInfo:
    version: str            # "1.4.0"
    tag: str                # "v1.4.0"
    notes: str              # 릴리스 노트(markdown)
    asset_name: str
    asset_url: str          # browser_download_url (또는 API asset url)
    asset_size: int


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


#: 내려받을 자산 확장자 — **앞쪽이 우선**. exe 한 장이 폴더째 붓는 것보다 안전하다.
ASSET_SUFFIXES = (".exe", ".zip")


def pick_asset(assets: list[dict]) -> dict | None:
    """릴리스 자산 목록에서 내려받을 것 하나. 없으면 None.

    같은 확장자가 여럿이면 **이름 순 첫 번째**를 쓴다 — 릴리스마다 자산 순서가
    흔들려도 고르는 결과가 같아야 한다(디버깅 가능성).
    """
    for suffix in ASSET_SUFFIXES:
        hit = sorted((a for a in assets
                      if str(a.get("name", "")).lower().endswith(suffix)),
                     key=lambda a: str(a.get("name", "")))
        if hit:
            return hit[0]
    return None


def check_for_update(current: str = __version__) -> UpdateInfo | None:
    """새 버전이 있으면 UpdateInfo, 없거나 확인 실패면 None.

    네트워크 오류·파싱 오류는 전부 삼킨다 — 업데이트 확인 실패가
    앱 실행을 막아서는 안 된다.
    """
    url = f"{API_BASE}/repos/{OWNER}/{REPO}/releases/latest"
    try:
        r = requests.get(url, headers=_headers(), timeout=TIMEOUT, verify=CA_BUNDLE)
        r.raise_for_status()
        rel = r.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        log.warning("업데이트 확인 실패: %s", e)
        return None

    tag = rel.get("tag_name", "")
    try:
        latest = Version(tag.lstrip("v"))
        cur = Version(current)
    except InvalidVersion:
        log.warning("버전 태그 해석 실패: %r", tag)
        return None
    if latest <= cur:
        return None

    asset = pick_asset(rel.get("assets", []))
    if asset is None:
        log.warning("릴리스 %s 에 내려받을 자산(.exe/.zip)이 없음", tag)
        return None

    return UpdateInfo(
        version=str(latest),
        tag=tag,
        notes=rel.get("body") or "(릴리스 노트 없음)",
        asset_name=asset["name"],
        asset_url=asset["browser_download_url"],
        asset_size=asset.get("size", 0),
    )


def download(info: UpdateInfo, dest, progress=None) -> None:
    """자산을 dest 경로로 스트리밍 다운로드. progress(done, total) 콜백."""
    with requests.get(
        info.asset_url, headers=_headers(), timeout=30,
        verify=CA_BUNDLE, stream=True,
    ) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", info.asset_size or 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
