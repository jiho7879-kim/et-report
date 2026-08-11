"""Excel 접근 계층 — 읽기는 반드시 xlwings, **단 한 번만**.

사내 보안상 openpyxl/pandas.read_excel을 쓸 수 없어 xlwings만 허용된다.
그런데 xlwings 호출은 COM으로 Excel 프로세스를 띄우므로 한 번에 1~3초가
든다 — 주요 병목이다. 그래서:

  1) 한 파일에서 여러 시트를 읽을 때는 **Excel을 한 번만 띄워** 모두 읽는다
  2) 읽은 결과는 parquet로 로컬 캐시에 저장한다 (%APPDATA%\\ETReport\\xlcache)
  3) 다음 호출은 파일의 mtime+size가 같으면 캐시(parquet)를 읽는다 —
     Excel을 아예 띄우지 않는다. 원본이 바뀌면 자동으로 무효화된다

캐시는 조회 편의를 위한 것이고 원본이 진실이다. 강제로 다시 읽으려면
force=True 또는 invalidate()를 쓴다.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import time
from pathlib import Path

import polars as pl

from etreport.paths import appdata_dir

log = logging.getLogger(__name__)

_MEM: dict[tuple[str, str, str], pl.DataFrame] = {}   # 프로세스 내 메모리 캐시
_SHEETS: dict[tuple[str, str], list[str]] = {}


# ── 캐시 위치 ────────────────────────────────────────────────
def cache_dir() -> Path:
    p = appdata_dir() / "xlcache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _stamp(path: str) -> str:
    """원본 변경 감지용 지문 — 수정시각 + 크기."""
    st = Path(path).stat()
    return f"{int(st.st_mtime)}_{st.st_size}"


def _slot(path: str, stamp: str, sheet: str) -> Path:
    key = hashlib.blake2b(
        f"{Path(path).resolve()}|{stamp}|{sheet}".lower().encode(),
        digest_size=10).hexdigest()
    return cache_dir() / f"{Path(path).stem}_{key}.parquet"


def invalidate(path: str | None = None) -> int:
    """캐시 삭제. path를 주면 그 파일 것만, 없으면 전부. 삭제 개수 반환."""
    n = 0
    stem = Path(path).stem if path else None
    for f in cache_dir().glob("*.parquet"):
        if stem is None or f.name.startswith(stem + "_"):
            f.unlink(missing_ok=True)
            n += 1
    if path:
        for k in [k for k in _MEM if k[0] == str(Path(path).resolve())]:
            _MEM.pop(k, None)
        for k in [k for k in _SHEETS if k[0] == str(Path(path).resolve())]:
            _SHEETS.pop(k, None)
    else:
        _MEM.clear()
        _SHEETS.clear()
    return n


def cache_stats() -> tuple[int, int]:
    files = list(cache_dir().glob("*.parquet"))
    return len(files), sum(f.stat().st_size for f in files)


# ── 공개 API ─────────────────────────────────────────────────
def sheet_names(path: str, force: bool = False) -> list[str]:
    """시트 목록. 같은 파일이면 Excel을 다시 띄우지 않는다."""
    key = (str(Path(path).resolve()), _stamp(path))
    if not force and key in _SHEETS:
        return _SHEETS[key]
    with _book(path) as (wb, _):
        names = [s.name for s in wb.sheets]
    _SHEETS[key] = names
    return names


def read_sheet(path: str, sheet: str | int = 0,
               force: bool = False) -> pl.DataFrame:
    """지정 시트를 header 1행 표로 읽는다. 캐시가 있으면 Excel을 안 띄운다."""
    return read_sheets(path, [sheet], force)[0]


def read_sheets(path: str, sheets: list[str | int],
                force: bool = False) -> list[pl.DataFrame]:
    """여러 시트를 **Excel 한 번만 띄워** 읽는다. 캐시된 것은 건너뛴다."""
    stamp = _stamp(path)
    resolved = str(Path(path).resolve())
    out: dict[int, pl.DataFrame] = {}
    todo: list[tuple[int, str | int]] = []

    for i, sh in enumerate(sheets):
        mkey = (resolved, stamp, str(sh))
        if not force and mkey in _MEM:
            out[i] = _MEM[mkey]
            continue
        slot = _slot(path, stamp, str(sh))
        if not force and slot.exists():
            try:
                df = pl.read_parquet(slot)
                _MEM[mkey] = df
                out[i] = df
                log.debug("xl 캐시 적중: %s [%s]", Path(path).name, sh)
                continue
            except Exception:                        # noqa: BLE001 — 깨진 캐시
                slot.unlink(missing_ok=True)
        todo.append((i, sh))

    if todo:
        t0 = time.monotonic()
        with _book(path) as (wb, _):
            for i, sh in todo:
                raw = wb.sheets[sh].used_range.options(ndim=2).value
                df = frame_from_rows(raw)
                out[i] = df
                _MEM[(resolved, stamp, str(sh))] = df
                try:
                    df.write_parquet(_slot(path, stamp, str(sh)))
                except Exception as e:               # noqa: BLE001
                    log.warning("xl 캐시 저장 실패: %s", e)
        log.info("Excel 읽기 %d시트 · %.1f초 (%s)",
                 len(todo), time.monotonic() - t0, Path(path).name)
    return [out[i] for i in range(len(sheets))]


# ── 내부 ─────────────────────────────────────────────────────
class _book:
    """Excel 앱 + 통합문서를 한 번만 열고 닫는 컨텍스트."""

    def __init__(self, path: str) -> None:
        self.path = path

    def __enter__(self):
        import xlwings as xw
        self.app = xw.App(visible=False, add_book=False)
        try:
            self.wb = self.app.books.open(self.path, read_only=True,
                                          update_links=False)
        except Exception:
            self.app.quit()
            raise
        return self.wb, self.app

    def __exit__(self, *exc) -> None:
        try:
            self.wb.close()
        finally:
            self.app.quit()


def frame_from_rows(raw: list[list]) -> pl.DataFrame:
    """2차원 셀 값 → DataFrame. 열 타입은 추론 대신 규칙으로 결정한다.

    xlwings 특성: 숫자 셀은 전부 float, 날짜는 datetime, 빈 셀은 None.
    - 전부 (None | 숫자)      → Float64
    - 그 외(문자·날짜 혼재 등) → Utf8
    이렇게 하지 않으면 ADDP FORM처럼 위쪽이 빈칸이고 아래에 수식 문자열이
    오는 열에서 스키마 추론이 실패한다.
    """
    if not raw:
        return pl.DataFrame()
    header_raw, rows = raw[0], raw[1:]

    names: list[str] = []
    for i, h in enumerate(header_raw):
        n = str(h).strip() if h is not None and str(h).strip() else f"col{i + 1}"
        base, k = n, 2
        while n in names:
            n = f"{base}_{k}"
            k += 1
        names.append(n)

    series: list[pl.Series] = []
    for i, name in enumerate(names):
        vals = [(r[i] if i < len(r) else None) for r in rows]
        numeric = all(
            v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))
            for v in vals)
        if numeric:
            series.append(pl.Series(name, [None if v is None else float(v)
                                           for v in vals], dtype=pl.Float64))
        else:
            def _s(v):
                if v is None:
                    return None
                if isinstance(v, float) and v.is_integer():
                    return str(int(v))          # 1.0 → "1" (코드·ID 열 보호)
                if isinstance(v, _dt.datetime):
                    return v.isoformat(sep=" ")
                if isinstance(v, _dt.date):
                    return v.isoformat()
                return str(v)
            series.append(pl.Series(name, [_s(v) for v in vals], dtype=pl.Utf8))
    return pl.DataFrame(series)


# ── 쓰기 (템플릿 되쓰기용) ───────────────────────────────────
def write_sheet(path: str, sheet: str | int, df: pl.DataFrame,
                backup: bool = True) -> str:
    """DataFrame으로 시트를 덮어쓴다. 쓰기도 xlwings만 사용.

    되돌릴 수 있도록 기본으로 .bak 사본을 남기고, 쓰기 후 캐시를 무효화한다.
    """
    import shutil

    import xlwings as xw

    src = Path(path)
    bak = ""
    if backup:
        bak = str(src.with_suffix(src.suffix + ".bak"))
        shutil.copy2(src, bak)

    app = xw.App(visible=False, add_book=False)
    try:
        wb = app.books.open(str(src))
        try:
            sht = wb.sheets[sheet]
            sht.clear_contents()
            sht.range((1, 1)).value = [df.columns, *df.rows()]
            sht.autofit("c")
            wb.save()
        finally:
            wb.close()
    finally:
        app.quit()
    invalidate(str(src))
    return bak
