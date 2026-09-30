"""적재가 끝난 wide 데이터를 CSV/SBDF로 내보낸다 — Qt가 없는 계층.

pipeline.run(§13 예약 실행 포함)이 적재 직후 프리셋 옵션대로 부르고, SQL
조회 창(sql_dialog)도 여기 copy_to를 공유한다. SBDF는 공식 spotfire 패키지
(spotfire.sbdf)를 먼저, 사내 레거시(sbdf)를 다음으로 찾고, 둘 다 없으면
경고만 남기고 건너뛴다 — 헤드리스 실행은 질문할 수 없으므로, 내보내기가
실패해도 이미 끝난 적재 결과를 되돌리거나 파이프라인을 실패시키지 않는다.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

#: 이 행 수를 넘으면 SBDF(메모리 경유) 내보내기는 헤드리스에서 건너뛴다 —
#: pandas 프레임으로 올리다 OOM으로 죽기 전에 막는다.
SBDF_WARN_ROWS = 2_000_000

# pandas 전체 프레임과 SBDF 변환은 같은 데이터를 여러 벌 붙들 수 있다.
SBDF_MAX_BYTES = 1 << 30


def preflight_sbdf(con, sql: str) -> int:
    """pandas/Arrow 할당 전에 안전하지 않은 전체 프레임 내보내기를 막는다.

    행 수·문자열 payload 집계는 DuckDB 안에서 끝낸다. 행 수만이 아니라 넓은
    프레임·객체 오버헤드·변환 중 복사본까지 예산에 넣는다. CSV/parquet COPY는
    직접 스트리밍하므로 이 가드를 일부러 거치지 않는다.
    """
    from etreport.data.extractor import available_memory_bytes

    query = sql.strip().rstrip(";")
    rows = int(con.execute(f"SELECT count(*) FROM (\n{query}\n)").fetchone()[0])
    if rows > SBDF_WARN_ROWS:
        raise ValueError(f"SBDF 행 수 상한({SBDF_WARN_ROWS:,}) 초과: {rows:,}행. "
                         "CSV 또는 parquet으로 저장하세요.")
    columns = con.execute(f"DESCRIBE (\n{query}\n)").fetchall()
    available = available_memory_bytes()
    budget = min(SBDF_MAX_BYTES, available // 4) if available else SBDF_MAX_BYTES
    estimated = rows * max(1, len(columns)) * 128 * 4
    if estimated > budget:
        raise ValueError("SBDF 메모리 안전 상한 초과 — CSV 또는 parquet으로 저장하세요.")
    payloads = []
    for name, dtype, *_ in columns:
        if any(token in dtype for token in ("[", "STRUCT", "MAP", "UNION")):
            raise ValueError("복합형 컬럼의 SBDF 메모리를 보장할 수 없습니다. "
                             "CSV 또는 parquet으로 저장하세요.")
        quoted = '"' + name.replace('"', '""') + '"'
        if dtype == "VARCHAR":
            payloads.append(f"coalesce(sum(octet_length(encode({quoted}))), 0)")
        elif dtype == "BLOB":
            payloads.append(f"coalesce(sum(octet_length({quoted})), 0)")
    if payloads:
        size = con.execute(f"SELECT {' + '.join(payloads)} FROM (\n{query}\n)").fetchone()[0]
        estimated += int(size) * 4
    if estimated > budget:
        raise ValueError("SBDF 메모리 안전 상한 초과 — CSV 또는 parquet으로 저장하세요.")
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
    with readonly_query(db_path) as con:
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
            from etreport.data.loader import readonly_query
            with readonly_query(db_path) as con:
                preflight_sbdf(con, "SELECT * FROM et_data")
                # pandas로 바로 받는다 — polars를 거치면 같은 표가 두 벌 생긴다
                full = con.execute("SELECT * FROM et_data").df()
            sbdf.export_data(full, str(base) + ".sbdf")
            del full
            saved.append(str(base) + ".sbdf")
        except Exception as e:                       # noqa: BLE001
            say(f"⚠ et_data.sbdf 저장 실패 — {e}")
    return saved
