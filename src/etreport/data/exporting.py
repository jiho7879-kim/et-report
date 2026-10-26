"""적재가 끝난 wide 데이터를 CSV/SBDF로 내보낸다 — Qt가 없는 계층.

pipeline.run(§13 예약 실행 포함)이 적재 직후 프리셋 옵션대로 부르고, SQL
조회 창(sql_dialog)도 여기 copy_to를 공유한다. SBDF는 공식 spotfire 패키지
(spotfire.sbdf)를 먼저, 사내 레거시(sbdf)를 다음으로 찾고, 둘 다 없으면
경고만 남기고 건너뛴다 — 헤드리스 실행은 질문할 수 없으므로, 내보내기가
실패해도 이미 끝난 적재 결과를 되돌리거나 파이프라인을 실패시키지 않는다.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)

#: SBDF 임시 parquet을 쓸 때의 DuckDB 상한 = 바닥값 + 열마다 고정해 두는 블록 몫
#: (실측 ≈0.5MB, 여유 두 배). 표 크기와 무관하다 — 캐시가 표만큼 자라지 않게.
COPY_BYTES_PER_COL = 1 << 20
COPY_BASE_BYTES = 512 << 20


def export_sbdf(db_path: str, sql: str, out: str, sbdf,
                report: Callable[[str], None] | None = None) -> int:
    """조회 결과를 SBDF 파일 하나로 — SQL 창·적재 후 저장 공용. 행 수를 돌려준다.

    **표 크기 + 열 묶음 하나**만큼만 메모리를 쓴다(직접 pandas로 저장할 때와 같은
    조건). 세 단계로 나눈다.
      ① DuckDB가 결과를 임시 parquet에 바로 쓴다(`COPY`, 행 순서 보존). 연결은
         `loader.private_query` — **열 수로 정한 작은 상한**의 별도 인스턴스다.
         예전처럼 80%로 풀어 두면 DuckDB가 읽은 블록을 그만큼 캐시로 쥐고 있어서
         표가 클수록 캐시도 같이 커졌다(600MB 표에서 COPY만 2GB).
      ② 연결을 닫는다 — 캐시가 인스턴스와 함께 사라진다.
      ③ parquet에서 열을 `loader.CHUNK_BYTES`씩 읽어 pandas 열로 붙이고 묶음은
         버린다. 사용자 SQL을 묶음마다 다시 돌리지 않는 이유: `ORDER BY` 없는
         조회는 실행마다 행 순서가 같다는 보장이 없어 열이 조용히 어긋난다.

    **메모리를 추정해 미리 거부하지 않는다** — 실패는 실제로 모자랄 때만 나고,
    그때는 메모리를 쓰지 않는 CSV·parquet을 안내한다. `report(라벨)`로 단계를
    알린다(수 GB면 분 단위라 단계가 안 보이면 멈춘 것과 구별되지 않는다).
    """
    import pandas as pd
    import pyarrow.parquet as pq

    from etreport.data.loader import CHUNK_BYTES, private_query
    say = report or (lambda _label: None)
    t0 = time.monotonic()
    body = sql.strip().rstrip(";")
    tmp = Path(out).with_name(Path(out).name + ".part.parquet")
    try:
        say("DB에서 읽어 임시 파일로 옮기는 중…")
        with private_query(db_path, f"{COPY_BASE_BYTES >> 20}MiB") as con:
            ncols = len(con.execute(f"SELECT * FROM (\n{body}\n) LIMIT 0")
                        .description)
            # 이 연결만의 인스턴스라 상한을 바꿔도 다른 조회와 상관없다
            need = COPY_BASE_BYTES + ncols * COPY_BYTES_PER_COL
            con.execute(f"SET memory_limit='{need >> 20}MiB'")
            path = str(tmp).replace("'", "''")
            con.execute(f"COPY (\n{body}\n) TO '{path}' (FORMAT PARQUET)")
        pf = pq.ParquetFile(tmp)
        names, rows = pf.schema_arrow.names, pf.metadata.num_rows
        step = max(1, CHUNK_BYTES // (8 * max(1, rows)))
        parts = []
        for i in range(0, len(names), step):
            say(f"열 {i + 1:,}–{min(i + step, len(names)):,} / {len(names):,} 읽는 중…")
            # 이름으로 골라도 된다 — COPY가 같은 이름 열을 `a_1`처럼 바꿔 쓴다
            tbl = pf.read(columns=names[i:i + step])
            # self_destruct: 열을 옮기는 대로 Arrow 버퍼를 푼다(묶음이 두 벌 살지 않게)
            parts.append(tbl.to_pandas(self_destruct=True, split_blocks=True))
            del tbl
        del pf
        # 열마다 블록이 따로라 붙일 때 값을 복사하지 않는다(블록을 합치지 않음)
        full = pd.concat(parts, axis=1) if parts else pd.DataFrame()
        del parts
        cols = full.shape[1]
        say(f"{rows:,}행 × {cols:,}열 읽음 ({time.monotonic() - t0:.0f}초) "
            "· SBDF 쓰는 중…")
        sbdf.export_data(full, out)
        del full
    except (MemoryError, duckdb.OutOfMemoryException) as e:
        raise MemoryError(
            f"SBDF로 옮길 메모리가 모자랍니다 — {e}\n\n"
            "CSV나 parquet으로 저장하면 메모리를 거의 쓰지 않습니다"
            "(Spotfire에서 같은 결과로 열립니다). 아니면 조건을 좁혀 주세요.") from e
    finally:
        tmp.unlink(missing_ok=True)
    log.info("SBDF 저장 %s — %d행, %.0f초", out, rows, time.monotonic() - t0)
    return rows


def import_sbdf() -> object | None:
    """SBDF 모듈을 돌려준다 — 공식 spotfire → 사내 레거시 순.

    둘 다 없으면 None (호출자가 경고만 남기고 건너뛴다).
    """
    try:
        import spotfire.sbdf as sbdf  # PyPI의 공식 spotfire 패키지
        return sbdf
    except ImportError:
        pass
    try:
        import sbdf  # 사내 구버전 라이브러리
        return sbdf
    except ImportError:
        return None


def copy_to(db_path: str, sql: str, out: str, fmt: str) -> str:
    """조회 결과를 DuckDB가 **파일로 직접** 쓰게 한다 (메모리 경유 없음).

    fmt는 "csv" 또는 "parquet". CSV는 엑셀에서 한글이 깨지지 않도록 BOM을
    앞에 붙인다 — DuckDB가 다 쓴 뒤 3바이트만 앞에 이어 붙인다.
    """
    from etreport.data.loader import readonly_query
    opts = ("FORMAT CSV, HEADER" if fmt == "csv" else "FORMAT PARQUET")
    target = Path(out)
    tmp = target.with_name(target.name + ".part") if fmt == "csv" else target
    with readonly_query(db_path, uncapped=True) as con:
        con.execute(f"COPY (\n{sql}\n) TO '{str(tmp).replace(chr(39), chr(39) * 2)}'"
                    f" ({opts})")
    if fmt == "csv":
        with target.open("wb") as dst:
            dst.write(b"\xef\xbb\xbf")
            with tmp.open("rb") as src:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
        tmp.unlink(missing_ok=True)
    return out


def save_wide(preset, on_log: Callable[[str], None] | None = None) -> list[str]:
    """적재가 끝난 wide et_data를 프리셋 옵션대로 CSV/SBDF로 내보낸다.

    대상은 ``SELECT * FROM et_data`` (적재 결과 그대로). 위치는
    ``preset.out_dir``(비면 DB 파일 옆), 파일명은 `et_data.csv` /
    `et_data.sbdf` 고정 — S3 업로드가 같은 이름을 기대한다.

    헤드리스(§13 예약 실행)에서도 돌므로 모달을 띄우지 않는다. 실패는 로그로만
    남기고 넘어간다 — 적재는 이미 끝났고, 내보내기 하나가 실패했다고 파이프라인
    전체를 실패시키면 안 된다.

    반환: 실제로 저장한 파일 경로 목록.
    """
    if not (preset.save_csv or preset.save_sbdf):
        return []
    say = on_log or log.info
    db_path = preset.db_path
    if not db_path:
        say("⚠ CSV/SBDF 저장 건너뜀 — DB 경로가 비어 있습니다")
        return []
    target_dir = Path(preset.out_dir) if preset.out_dir else Path(db_path).parent
    target_dir.mkdir(parents=True, exist_ok=True)
    base = target_dir / "et_data"
    saved: list[str] = []

    if preset.save_csv:
        try:
            copy_to(db_path, "SELECT * FROM et_data", str(base) + ".csv", "csv")
            saved.append(str(base) + ".csv")
        except Exception as e:                       # noqa: BLE001
            say(f"⚠ et_data.csv 저장 실패 — {e}")

    if preset.save_sbdf:
        sbdf = import_sbdf()
        if sbdf is None:
            say("⚠ SBDF 저장 건너뜀 — spotfire/sbdf 라이브러리를 찾을 수 없습니다")
            return saved
        try:
            export_sbdf(db_path, "SELECT * FROM et_data", str(base) + ".sbdf",
                        sbdf, report=say)
            saved.append(str(base) + ".sbdf")
        except Exception as e:                       # noqa: BLE001
            say(f"⚠ et_data.sbdf 저장 실패 — {e}")
    return saved
