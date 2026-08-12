"""부팅(`etreport.app`)의 회귀 — 배포 exe에서만 터지는 것들을 여기서 잡는다.

- 콘솔 없는 --windowed exe(`sys.stderr is None`)에서 로깅이 죽지 않는가
- 처리되지 않은 예외가 로그에 남는가 (배포본에는 트레이스백을 볼 곳이 없다)
- 카탈로그 캐시가 깨져도 앱이 뜨는가
"""
from __future__ import annotations

import json
import logging
import sys

import pytest

from etreport import app as boot


@pytest.fixture
def clean_logging():
    """루트 로거·excepthook을 원래대로 돌려놓는다 (전역이라 다른 테스트가 물린다)."""
    root = logging.getLogger()
    saved, saved_level, saved_hook = root.handlers[:], root.level, sys.excepthook
    root.handlers = []
    yield root
    for h in root.handlers:
        h.close()
    root.handlers, root.level, sys.excepthook = saved, saved_level, saved_hook


def test_logging_works_without_console(appdata, clean_logging, monkeypatch):
    """--windowed exe에는 stderr가 없다 — StreamHandler를 붙이면 첫 로그에서 터진다."""
    monkeypatch.setattr(sys, "stderr", None)

    boot._setup_logging("INFO")
    logging.getLogger("etreport").info("부팅 확인")

    handlers = clean_logging.handlers
    assert all(not isinstance(h, logging.StreamHandler)
               or isinstance(h, logging.FileHandler) for h in handlers)
    text = (appdata / "logs" / "etreport.log").read_text(encoding="utf-8")
    assert "부팅 확인" in text


def test_setup_logging_is_idempotent(appdata, clean_logging):
    """두 번 불러도 핸들러가 쌓이지 않는다 (같은 줄이 두 번 찍히면 로그를 못 읽는다)."""
    boot._setup_logging("INFO")
    n = len(clean_logging.handlers)
    boot._setup_logging("DEBUG")
    assert len(clean_logging.handlers) == n


def test_debug_level_does_not_pull_in_matplotlib_chatter(appdata, clean_logging):
    boot._setup_logging("DEBUG")
    assert logging.getLogger("matplotlib").getEffectiveLevel() >= logging.WARNING


def test_excepthook_logs_and_does_not_raise(appdata, clean_logging, caplog):
    """QApplication이 없어도(=창 뜨기 전) 훅이 조용히 로그만 남긴다."""
    log = logging.getLogger("etreport.test")
    boot._install_excepthook(log)

    with caplog.at_level(logging.CRITICAL):
        for _ in range(2):                     # 같은 오류가 반복돼도 훅이 죽지 않는다
            try:
                raise ValueError("일부러 낸 오류")
            except ValueError:
                sys.excepthook(*sys.exc_info())

    assert sum("일부러 낸 오류" in r.message + str(r.exc_info[1])
               for r in caplog.records) == 2


def test_keyboard_interrupt_passes_through(appdata, clean_logging):
    """Ctrl+C는 기본 훅에 그대로 넘긴다 — 오류로 취급하지 않는다."""
    seen = []
    monkey = sys.excepthook
    sys.excepthook = lambda *a: seen.append(a)
    try:
        boot._install_excepthook(logging.getLogger("etreport.test"))
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    finally:
        sys.excepthook = monkey
    assert len(seen) == 1


def test_catalog_survives_corrupt_cache(appdata):
    """캐시 JSON이 깨져도 앱은 뜬다 — 캐시를 버리고 조회, 조회도 안 되면 기본 목록."""
    from etreport.paths import catalog_cache_file

    catalog_cache_file().write_text("{망가진 JSON", encoding="utf-8")

    catalog = boot._prepare_catalog(logging.getLogger("etreport.test"))

    assert catalog.get("line_id") is not None          # 조건 빌더가 쓸 컬럼이 있다
    assert catalog.fetched_at == "(기본값)"            # bdq 없음 → 기본 목록


def test_catalog_prefers_cache(appdata):
    """정상 캐시가 있으면 그대로 쓴다 (bdq 조회는 느리다 — 최초 1회만)."""
    from etreport.paths import catalog_cache_file, write_json_atomic

    write_json_atomic(catalog_cache_file(),
                      {"fetched_at": "2026-08-12 09:00",
                       "columns": [{"name": "only_col", "dtype": "STRING"}]})

    catalog = boot._prepare_catalog(logging.getLogger("etreport.test"))

    assert [c.name for c in catalog.columns] == ["only_col"]
    assert json.loads(catalog_cache_file().read_text(encoding="utf-8"))["columns"]
