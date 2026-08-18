"""이 실행 파일이 **언제 · 어느 소스로** 만들어졌는지.

"코드를 고쳤는데 exe 결과가 그대로다"를 눈으로 확인할 수 있어야 한다. 빌드
스크립트가 `assets/build_info.json`을 만들어 번들에 싣고, 앱은 부팅 로그와
[도움말] → [정보]에 그 값을 적는다. 값이 예전 그대로면 **새 exe가 아니거나
예전 exe를 실행하고 있는 것**이고, 값이 바뀌었는데 동작이 그대로면 그때는
진짜 코드 문제다 — 둘을 구분할 방법이 없던 것이 문제였다.

번들에 파일이 없으면(=소스 실행) 버전만 적고 나머지는 비운다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from etreport import __version__

log = logging.getLogger(__name__)

RESOURCE = "assets/build_info.json"


@dataclass(frozen=True)
class BuildInfo:
    version: str = __version__
    built_at: str = ""          # ISO8601 (로컬 시각)
    commit: str = ""            # git 짧은 해시 (+dirty)
    mode: str = "source"        # source | onefile | onedir

    def label(self) -> str:
        """한 줄 표기 — 상태 표시줄·로그·[정보] 창이 함께 쓴다."""
        parts = [f"v{self.version}"]
        if self.commit:
            parts.append(self.commit)
        if self.built_at:
            parts.append(self.built_at)
        parts.append({"source": "소스 실행", "onefile": "단일 exe",
                      "onedir": "폴더 배포"}.get(self.mode, self.mode))
        return " · ".join(parts)


def _frozen_mode() -> str:
    import sys
    if not getattr(sys, "frozen", False):
        return "source"
    # onefile은 임시 폴더에 풀고 exe와 다른 자리를 가리킨다 — 그것으로 구분한다.
    from etreport import resources
    b = resources.bundle_dir()
    from pathlib import Path
    return "onedir" if b and b.parent == Path(sys.executable).parent else "onefile"


_cached: BuildInfo | None = None


def get() -> BuildInfo:
    global _cached
    if _cached is not None:
        return _cached
    from etreport import resources
    mode = _frozen_mode()
    p = resources.find(RESOURCE)
    if p is None:
        _cached = BuildInfo(mode=mode)
        return _cached
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:     # 있으나 마나 한 정보다
        log.debug("build_info.json을 읽지 못했습니다(무시): %s", e)
        _cached = BuildInfo(mode=mode)
        return _cached
    _cached = BuildInfo(
        version=str(raw.get("version") or __version__),
        built_at=str(raw.get("built_at") or ""),
        commit=str(raw.get("commit") or ""),
        mode=str(raw.get("mode") or mode),
    )
    return _cached
