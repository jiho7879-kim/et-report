"""작업 세트 — [적용]이 고른 범위를 임시 parquet 한 벌로 떠 둔다(2026-10-13).

[적용]은 `행 × item` 프레임을 통째로 메모리에 올렸다. lot 두 개 · full map이면
그것만 수 GB였고, 제외·그룹 분할·집계가 저마다 한 벌씩 더 복사해서 lot 하나도
못 여는 일이 있었다. 이제 DuckDB가 고른 lot·item을 `COPY … TO parquet`으로
흘려 쓰고(파이썬을 거치지 않는다), 메모리에는 키 프레임만 둔다. 값은 쓰는 곳이
필요한 열만 읽는다(`loader.frame`) — plot 하나는 x·y 두 열, 요약 표는 묶음 단위.

파일은 `%APPDATA%\\ETReport\\workset\\`에 두고 **최근 `KEEP`벌만** 남긴다.
이름은 DB 정체(경로·크기·수정 시각)와 SQL의 해시라, 같은 조합이면 [적용]을
다시 눌러도 다시 뜨지 않는다. DB에 이어 적재하면 수정 시각이 바뀌어 새로 뜬다.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)

#: 남겨 둘 작업 세트 수. 지금 것 + 바로 앞의 것(lot을 바꿔 보다 되돌아올 때).
KEEP = 2
#: 행 그룹 하나의 대략 크기 — DuckDB가 parquet을 쓰며 붙잡는 버퍼가 열 수에
#: 비례하지 않게 행 수를 열 수에 맞춰 줄인다(item 1,000열이면 4천 행씩).
ROW_GROUP_BYTES = 32 << 20


def folder() -> Path:
    from etreport.paths import appdata_dir
    return appdata_dir() / "workset"


@dataclass
class Workset:
    db_path: str
    sql: str
    path: Path
    items: list[str]           # 이 파일에 든 item 열(키 열 제외)

    def keys(self, cols: list[str]) -> pl.DataFrame:
        return pl.read_parquet(self.path, columns=cols)

    def read(self, cols: list[str]) -> pl.DataFrame:
        """key + `cols`. 파일이 지워졌으면(다른 창의 정리·사용자) 다시 뜬다."""
        if not self.path.exists():
            from etreport.data.loader import readonly_query
            log.info("작업 세트가 없어 다시 만듭니다: %s", self.path.name)
            with readonly_query(self.db_path) as con:
                _write(con, self.sql, self.path, len(self.items))
        return pl.read_parquet(self.path, columns=["key", *cols])


def open_set(con, db_path: str, sql: str, reserved: tuple[str, ...]) -> Workset:
    """`sql` 결과의 작업 세트 — 같은 DB·같은 SQL이면 있는 파일을 그대로 쓴다."""
    st = Path(db_path).stat()
    ident = f"{Path(db_path).resolve()}|{st.st_size}|{st.st_mtime_ns}|{sql}"
    path = folder() / f"ws_{hashlib.sha1(ident.encode()).hexdigest()[:16]}.parquet"
    if path.exists():
        os.utime(path)                     # 최근에 쓴 것으로 — 정리에서 살아남게
        log.info("작업 세트 재사용: %s", path.name)
    else:
        _write(con, sql, path, None)
    items = [c for c in pl.read_parquet_schema(path) if c not in reserved]
    _cleanup(path)
    return Workset(db_path, sql, path, items)


def _write(con, sql: str, path: Path, n_cols: int | None) -> None:
    """COPY로 흘려 쓴다 — 다 쓴 뒤에 이름을 바꿔, 반쯤 쓴 파일이 남지 않게."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if n_cols is None:
        n_cols = len(con.execute(f"DESCRIBE ({sql})").fetchall())
    rows = max(2048, min(122_880, ROW_GROUP_BYTES // (8 * max(1, n_cols))))
    tmp = path.with_suffix(".tmp")
    t0 = time.monotonic()
    try:
        con.execute(f"COPY ({sql}) TO '{str(tmp).replace(chr(39), chr(39) * 2)}' "
                    f"(FORMAT parquet, ROW_GROUP_SIZE {rows})")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    log.info("작업 세트 생성 %s — %.1f MB · %.1f초", path.name,
             path.stat().st_size / 1e6, time.monotonic() - t0)


def _cleanup(current: Path) -> None:
    """최근 `KEEP`벌만 남긴다. 하루 지난 `.tmp`(중간에 죽은 흔적)도 지운다."""
    files = sorted(current.parent.glob("ws_*.parquet"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    old = [p for p in files if p != current][KEEP - 1:]
    old += [p for p in current.parent.glob("ws_*.tmp")
            if time.time() - p.stat().st_mtime > 86_400]
    for p in old:
        try:
            p.unlink()
        except OSError as e:               # 다른 창이 읽는 중일 수 있다 — 다음에
            log.debug("작업 세트 정리 실패(%s): %s", p.name, e)
