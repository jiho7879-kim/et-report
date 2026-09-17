"""추출 파이프라인 — **UI 없이도 도는 한 벌**.

    리포메터 로드 → 추출(일×item 청크) → 리포메팅 → DuckDB 적재 → staging 정리

예전에는 이 순서가 `ui/data_ws.py`의 QThread 안에 통째로 들어 있었다. 그래서
화면 없이 돌릴 방법이 없었고, 예약 실행(§13)을 붙이려면 같은 코드를 한 벌 더
쓰는 수밖에 없었다 — 그러면 두 경로가 조용히 갈린다. 여기로 옮겨 두면 화면은
진행 신호만 받아 그리고, 스케줄러는 같은 함수를 그냥 부른다.

Qt에 의존하지 않는다. 진행 알림은 콜백 두 개로 받는다.
  `on_log(str)`               한 줄 로그
  `on_step(라벨, 완료, 전체)`  진행률 (전체가 0이면 라벨만)
  `should_stop() -> bool`     중지 요청 확인 (없으면 끝까지 돈다)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class ExtractResult:
    rows: int = 0                 # 적재된 행 수
    seconds: float = 0.0
    raw_rows: int = 0             # 추출 원본(long) 행 수
    files: int = 0                # 청크 파일 수
    filled: int = 0               # 이미 있던 행에 빠진 item만 채운 수(§6)

    def summary(self) -> str:
        fill = f" · 빈 칸 채움 {self.filled:,}행" if self.filled else ""
        return (f"적재 {self.rows:,}행{fill} (원본 {self.raw_rows:,}행 · "
                f"청크 {self.files}개) · {self.seconds:.1f}초")


def _noop_log(_msg: str) -> None:
    pass


def _noop_step(_label: str, _done: int, _total: int) -> None:
    pass


def run(preset, d_from: date, d_to: date, catalog,
        on_log=None, on_step=None, should_stop=None) -> ExtractResult:
    """추출부터 적재까지 한 번에. 실패는 예외로 올린다(부르는 쪽이 표시한다).

    `preset`은 `config.settings.ExtractPreset` — db_path·reformatter_path·
    conditions를 갖고 있으면 되므로, 같은 모양이면 무엇이든 받는다.
    """
    on_log = on_log or _noop_log
    on_step = on_step or _noop_step
    stop = should_stop or (lambda: False)

    import polars as pl

    from etreport.data import extractor
    from etreport.data.db import Store, pivot_and_load
    from etreport.data.reformatter import apply as rf_apply
    from etreport.data.reformatter import load as rf_load
    from etreport.paths import cleanup_staging, staging_dir

    t0 = time.monotonic()
    res = ExtractResult()

    # 1) 리포메터 -------------------------------------------------
    t = time.monotonic()
    on_step("리포메터 읽는 중", 0, 0)
    sheet = preset.reformatter_sheet or 0
    on_log(f"리포메터 열기: {Path(preset.reformatter_path).name}"
           + (f" [{sheet}]" if isinstance(sheet, str) else ""))
    rf = rf_load(preset.reformatter_path, sheet)
    if rf.errors:
        raise RuntimeError("리포메터를 쓸 수 없습니다:\n"
                           + "\n".join(rf.report_lines()[:8]))
    on_log(f"  REAL {len(rf.reals())} · ADDP {len(rf.addps())}"
           f"  ({time.monotonic() - t:.1f}초)")
    for w in rf.warnings[:20]:
        on_log(f"  ⚠ {w.row}행 {w.alias}: {w.message}")
    if len(rf.warnings) > 20:
        on_log(f"  ⚠ 외 {len(rf.warnings) - 20}건 더 제외")

    # 2) 추출 ------------------------------------------------------
    t = time.monotonic()
    rf_items = [r.itemid for r in rf.reals() if r.itemid]
    groups = extractor.item_groups(rf_items)
    if len(groups) > 1:
        on_log(f"조회 item {len(rf_items):,}개 (REAL만, ADDP 제외) — "
               f"상한 초과로 {len(groups)}개 그룹 분할")
    units = extractor.plan_units(d_from, d_to, rf_items)
    on_log(f"추출 시작 — {d_from} ~ {d_to} · 청크 {len(units)}개 "
           f"· 워커 {extractor.N_WORKERS}")
    on_step("추출 중", 0, len(units))

    def on_prog(done: int, total: int, label: str) -> None:
        on_step("추출 중", done, total)
        on_log(f"  청크 {done}/{total} 완료  ({label})")

    reuse = bool(getattr(preset, "reuse_staging", False))
    if reuse:
        on_log("  추출 원본 재사용 켜짐 — 같은 조건으로 받아 둔 parquet은 다시 "
               "조회하지 않습니다")
    files = extractor.extract_to_parquet(
        preset.conditions, d_from, d_to, catalog, staging_dir(),
        on_prog, stop, item_ids=rf_items, reuse=reuse)
    if not files:
        raise RuntimeError("중지되었거나 결과가 없습니다")
    res.files = len(files)
    res.raw_rows = sum(pl.scan_parquet(str(f)).select(pl.len()).collect().item()
                       for f in files)
    on_log(f"추출 완료 — {res.raw_rows:,}행 (long) · {time.monotonic() - t:.1f}초")
    on_log("  온도 보정 적용 — 5단위 정수로 맞춰 적재합니다 (23.9 → 25)")

    # 3) 리포메팅 --------------------------------------------------
    t = time.monotonic()
    on_log(f"리포메팅 시작 — 파일 {len(files)}개 · ADDP {len(rf.addps())}개")
    on_step("리포메팅 중", 0, len(files))
    done_rows = 0
    reformatted: list[Path] = []
    for i, f in enumerate(files, 1):
        ft = time.monotonic()
        src = pl.read_parquet(f)

        def prog(stage: str, d: int, tot: int, _i=i) -> None:
            # ADDP가 많으면 어느 item에서 시간이 가는지 보이게
            on_step(f"리포메팅 {_i}/{len(files)} · {stage}", d, tot or 1)

        out = rf_apply(rf, src, on_progress=prog)
        # 원본(추출 결과)은 남긴다 — 추출이 가장 비싼 단계라, 리포메터를 고쳐서
        # 다시 돌릴 때 재추출 없이 이 파일만 다시 쓰면 된다.
        rf_file = f.with_name(f.stem + "_rf.parquet")
        out.write_parquet(rf_file)
        reformatted.append(rf_file)
        done_rows += out.height
        on_log(f"  파일 {i}/{len(files)}  {src.height:,}행 → {out.height:,}행"
               f"  ({time.monotonic() - ft:.1f}초)")
    on_log(f"리포메팅 완료 — {done_rows:,}행 · {time.monotonic() - t:.1f}초")

    # 4) 적재 ------------------------------------------------------
    t = time.monotonic()
    on_log(f"DuckDB 적재: {Path(preset.db_path).name}")
    on_step("적재 중", 0, 100)
    last = [0.0]

    def load_prog(d: int, tot: int) -> None:
        on_step("적재 중", d, tot)
        now = time.monotonic()
        if now - last[0] > 2.0 or d == tot:        # 2초마다 한 줄
            last[0] = now
            on_log(f"  버킷 {d}/{tot}")

    # 적재 연결은 반드시 닫는다 — 열려 있으면 DuckDB 쓰기 잠금이 남아 곧바로
    # 이어지는 [분석] 자동 연결(읽기 전용 열기)이 실패한다.
    store = Store(preset.db_path)
    try:
        res.rows = pivot_and_load(store, reformatted, on_progress=load_prog)
        res.filled = store.filled
    finally:
        store.close()
    on_log(f"적재 완료 — {res.rows:,}행"
           + (f" · 기존 행 {res.filled:,}개의 빈 item 채움" if res.filled else "")
           + f" · {time.monotonic() - t:.1f}초")

    # 4.5) 저장 옵션 — 적재 결과를 CSV/SBDF로 내보낸다 (스케줄러도 이 훅을 탄다)
    if preset.save_csv or preset.save_sbdf:
        from etreport.data.exporting import save_wide
        save_wide(preset, on_log=on_log)

    # 5) 뒷정리 — staging은 놔두면 하루 수십 MB씩 쌓인다
    gone = cleanup_staging()
    if gone:
        on_log(f"staging 정리 — 오래된 파일 {gone}개 삭제")

    res.seconds = time.monotonic() - t0
    on_log(f"── 전체 {res.seconds:.1f}초 ──")
    return res
