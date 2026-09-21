"""추출 메모리 보호 — OOM은 재시도하거나 다음 청크를 시작하지 않는다."""
from __future__ import annotations

import threading
import time
from datetime import date

import polars as pl
import pytest

from etreport.config.catalog import Catalog
from etreport.config.settings import Condition
from etreport.data import extractor


def _condition() -> list[Condition]:
    return [Condition("line_id", "L1", required=True)]


def test_memory_error_is_not_retried(tmp_path, monkeypatch):
    calls = 0

    def oom(_sql):
        nonlocal calls
        calls += 1
        raise MemoryError("no memory")

    monkeypatch.setattr(extractor, "_fetch", oom)
    with pytest.raises(RuntimeError, match="메모리가 부족"):
        extractor.extract_to_parquet(
            _condition(), date(2026, 1, 1), date(2026, 1, 1), Catalog(), tmp_path)
    assert calls == 1


def test_oom_stops_unscheduled_units(tmp_path, monkeypatch):
    calls: list[str] = []

    def oom(_sql):
        calls.append("fetch")
        raise MemoryError("no memory")

    monkeypatch.setattr(extractor, "_fetch", oom)
    monkeypatch.setattr(extractor, "N_WORKERS", 1)
    with pytest.raises(RuntimeError, match="메모리가 부족"):
        extractor.extract_to_parquet(
            _condition(), date(2026, 1, 1), date(2026, 1, 3), Catalog(), tmp_path)
    assert calls == ["fetch"]


def test_worker_cap_follows_the_available_cores():
    """4개 고정이면 16코어 PC가 4개만 쓴다 — 상한은 가용 코어의 60%다."""
    assert [extractor.plan_cpu_workers(n) for n in (1, 2, 4, 8, 16, 32)] \
        == [1, 1, 2, 5, 10, 19]
    assert extractor.plan_cpu_workers(64, ratio=1.0) == 64
    assert extractor.plan_cpu_workers() == extractor.N_WORKERS
    assert extractor.available_cpus() >= 1


def test_plan_workers_has_a_safe_lower_bound():
    assert extractor.plan_workers(100, 10_000) == 1
    assert extractor.plan_workers(None, None) == extractor.N_WORKERS


def test_plan_group_size_only_shrinks_when_the_budget_is_passed():
    # 예산 안이면 손대지 않는다 — 실측 규모(20만행/일)는 여기 걸리지 않는다.
    assert extractor.plan_group_size(200_000, 1000, max_rows=2_000_000) is None
    assert extractor.plan_group_size(None, 1000) is None
    assert extractor.plan_group_size(200_000, None) is None
    # 예산의 4배면 그룹도 1/4로.
    assert extractor.plan_group_size(8_000_000, 1000, max_rows=2_000_000) == 250
    # 한 item만으로 예산을 넘겨도 0으로 내려가지 않는다.
    assert extractor.plan_group_size(9_000_000, 1, max_rows=2_000_000) == 1


def test_resplit_keeps_every_item_and_regroups_by_chunk():
    units = extractor.plan_units(date(2026, 1, 1), date(2026, 1, 2),
                                 [f"I{i}" for i in range(10)])
    assert [u.item_ids for u in units] == [[f"I{i}" for i in range(10)]] * 2

    out = extractor.resplit_units(units, 4)
    per_chunk: dict = {}
    for u in out:
        per_chunk.setdefault(u.chunk, []).extend(u.item_ids)
    assert len(per_chunk) == 2                       # 기간 청크는 그대로 둘
    for items in per_chunk.values():                 # item은 하나도 빠지지 않는다
        assert items == [f"I{i}" for i in range(10)]
    assert [len(u.item_ids) for u in out] == [4, 4, 2, 4, 4, 2]
    assert [u.n_groups for u in out] == [3] * 6      # 라벨도 새 그룹 수를 따른다


def test_workers_ramp_up_after_the_first_measured_chunk(tmp_path, monkeypatch):
    """첫 청크는 단독, 그 뒤는 병렬 — 풀을 1로 만들어 두면 영영 직렬이 된다."""
    lock = threading.Lock()
    live = 0
    peak = 0

    def fetch(_sql):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)                             # 겹칠 틈을 준다
        with lock:
            live -= 1
        return pl.DataFrame({"item_id": ["I0"], "et_value": [1.0]})

    monkeypatch.setattr(extractor, "_fetch", fetch)
    extractor.extract_to_parquet(
        _condition(), date(2026, 1, 1), date(2026, 1, 8), Catalog(), tmp_path)
    assert peak > 1


def test_oversized_first_chunk_shrinks_the_remaining_units(tmp_path, monkeypatch):
    """첫 청크가 예산을 넘으면 그 다음 조회부터 item이 줄어야 한다."""
    seen: list[int] = []

    def fetch(sql):
        seen.append(sql.count("'I"))                 # IN 목록의 item 개수
        rows = 40 if len(seen) == 1 else 4
        return pl.DataFrame({"item_id": ["I0"] * rows,
                             "et_value": [1.0] * rows})

    monkeypatch.setattr(extractor, "_fetch", fetch)
    monkeypatch.setattr(extractor, "MAX_ROWS_PER_UNIT", 10)
    files = extractor.extract_to_parquet(
        _condition(), date(2026, 1, 1), date(2026, 1, 2), Catalog(), tmp_path,
        item_ids=[f"I{i}" for i in range(8)])

    assert seen[0] == 8                              # 첫 조회는 8 item 한 벌
    # 40행 / 예산 10 = 1/4 → 남은 둘째 날은 2 item씩 네 번으로 갈린다.
    assert sorted(seen[1:]) == [2, 2, 2, 2]
    assert len(files) == len(seen)
