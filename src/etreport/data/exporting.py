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

def fetch_frame(db_path: str, sql: str):
    """조회 결과를 pandas 프레임 하나로 — SBDF 저장(SQL 창·적재 후 저장) 공용.

    **메모리를 추정해 미리 거부하지 않는다.** 한때 가용 메모리 × 비율로 사전
    점검을 두었는데, 추정이 보수적이라 예전 버전에서 잘 되던 수 GB 내보내기까지
    막았다. 이제 실패는 실제로 모자랄 때만 나고(예외가 그대로 올라간다), DuckDB
    메모리 상한(`readonly_config`)은 결과를 pandas로 옮기는 몫을 막지 않는다.
    """
    from etreport.data.loader import readonly_query
    with readonly_query(db_path) as con:
        return con.execute(sql.strip().rstrip(";")).df()


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
            # pandas로 바로 받는다 — polars를 거치면 같은 표가 두 벌 생긴다
            full = fetch_frame(db_path, "SELECT * FROM et_data")
            sbdf.export_data(full, str(base) + ".sbdf")
            del full
            saved.append(str(base) + ".sbdf")
        except Exception as e:                       # noqa: BLE001
            say(f"⚠ et_data.sbdf 저장 실패 — {e}")
    return saved
