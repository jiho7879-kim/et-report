"""데모 번들 — 데모 데이터를 **진짜 파일**로 떨어뜨린다.

`demo_data`의 표를 파일로 만들어, 데모에서도 실제 사용 흐름(파일 고르기 →
[적용] → 그리기 → 내보내기)을 그대로 밟게 한다. DuckDB는 손으로 만들지 않고
**리포메팅 → 피벗 적재**라는 실제 경로로 만든다 — 그래야 §10.1 병합·retest·
온도 보정·ABSOLUTE 재적용이 데모에서도 진짜로 검증된다.

만들어지는 것 (`%APPDATA%\\ETReport\\demo`, 없으면 `~/ETReport/demo`)
--------------------------------------------------------------------
    데모_ET.duckdb          et_data — 추출·리포메팅·적재를 거친 결과
    데모_리포메터.csv/.xlsx  REAL 13 · ADDP 12
    데모_템플릿.xlsx         PLOT·TABLE 두 시트 (한 파일 두 시트 경로)
    데모_plot템플릿.csv      Excel 없는 PC(리눅스·WSL)용
    데모_table템플릿.csv
    데모_실험조건.csv        lot·wafer × M1·M5·M8
    데모_안내.md             무엇을 눌러 무엇을 확인하는지

xlsx는 xlsxwriter가 있을 때만 만든다(읽기는 사내 PC의 Excel/xlwings 담당).
Excel이 없는 곳에서는 csv를 그대로 고르면 된다 — `xlio`가 csv도 정식 입력으로
받는다.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from etreport import demo_data
from etreport.paths import appdata_dir

log = logging.getLogger(__name__)

DB_NAME = "데모_ET.duckdb"
RF_CSV = "데모_리포메터.csv"
RF_XLSX = "데모_리포메터.xlsx"
TPL_XLSX = "데모_템플릿.xlsx"
PLOT_CSV = "데모_plot템플릿.csv"
TABLE_CSV = "데모_table템플릿.csv"
SPLIT_CSV = "데모_실험조건.csv"
GUIDE = "데모_안내.md"
PLOT_SHEET, TABLE_SHEET = "PLOT", "TABLE"


@dataclass
class DemoBundle:
    """번들 파일 경로 묶음. `stage_*`는 이 PC에서 **읽을 수 있는** 경로다."""
    root: Path
    db: Path
    rf_csv: Path
    rf_xlsx: Path | None
    tpl_xlsx: Path | None
    plot_csv: Path
    table_csv: Path
    split_csv: Path
    guide: Path
    made: list[str] = field(default_factory=list)

    # ── 이 PC에서 고를 경로 ─────────────────────────────────
    @property
    def excel_ready(self) -> bool:
        """Excel(xlwings)로 xlsx를 읽을 수 있는 PC인가."""
        return _has_excel() and self.tpl_xlsx is not None

    def stage_reformatter(self) -> tuple[str, str]:
        if self.excel_ready and self.rf_xlsx:
            return str(self.rf_xlsx), ""
        return str(self.rf_csv), ""

    def stage_templates(self) -> tuple[str, str, str, str]:
        """(plot 경로, plot 시트, table 경로, table 시트)."""
        if self.excel_ready and self.tpl_xlsx:
            return (str(self.tpl_xlsx), PLOT_SHEET,
                    str(self.tpl_xlsx), TABLE_SHEET)
        return str(self.plot_csv), "", str(self.table_csv), ""


def bundle_dir() -> Path:
    return appdata_dir() / "demo"


def _has_excel() -> bool:
    try:
        import xlwings  # noqa: F401
    except Exception:                      # noqa: BLE001 — 리눅스·WSL
        return False
    import sys
    return sys.platform == "win32"


# ── 만들기 ───────────────────────────────────────────────────
def build(root: str | Path | None = None, force: bool = False) -> DemoBundle:
    """번들을 만들고(이미 있으면 그대로 두고) 경로 묶음을 돌려준다.

    force=True면 DB까지 다시 만든다. 파일 하나가 빠져 있으면 그것만 다시 쓴다.
    """
    root = Path(root) if root else bundle_dir()
    root.mkdir(parents=True, exist_ok=True)

    b = DemoBundle(
        root=root, db=root / DB_NAME, rf_csv=root / RF_CSV,
        rf_xlsx=None, tpl_xlsx=None,
        plot_csv=root / PLOT_CSV, table_csv=root / TABLE_CSV,
        split_csv=root / SPLIT_CSV, guide=root / GUIDE)

    rf_frame = demo_data.reformatter_frame()
    _write_csv(b.rf_csv, rf_frame, force, b)
    _write_csv(b.plot_csv, demo_data.plot_frame(), force, b)
    _write_csv(b.table_csv, demo_data.table_frame(), force, b)
    _write_csv(b.split_csv, demo_data.split_frame(), force, b)
    _write_text(b.guide, guide_text(), force, b)

    b.rf_xlsx = _write_xlsx(root / RF_XLSX, {"REFORMATTER": rf_frame}, force, b)
    b.tpl_xlsx = _write_xlsx(root / TPL_XLSX,
                             {PLOT_SHEET: demo_data.plot_frame(),
                              TABLE_SHEET: demo_data.table_frame()}, force, b)

    if force or not b.db.exists():
        build_db(b.db)
        b.made.append(b.db.name)
    return b


def build_db(path: str | Path, long: pl.DataFrame | None = None) -> Path:
    """데모 DuckDB — **실제 파이프라인**(리포메팅 → 피벗 적재)으로 만든다.

    추출만 건너뛴다(bdq가 없으니 합성 long을 직접 넣는다). 그 뒤 경로는 사내
    PC에서 도는 것과 같은 코드라 병합·중복 차단·버킷 계획이 그대로 걸린다.
    """
    from etreport.data import db as dbmod
    from etreport.data import reformatter as R
    from etreport.data.extractor import normalize_schema

    path = Path(path)
    if path.exists():
        path.unlink()
    staging = path.parent / "_raw"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    rf = demo_data.reformatter()
    long = demo_data.long_frame() if long is None else long
    long = normalize_schema(long)            # 온도 5단위 보정·타입 고정
    files: list[Path] = []
    # 추출 청크(하루치)와 같은 모양으로 나눠 둔다 — 여러 parquet을 합쳐 적재하는
    # 경로(scan_parquet + key_hash dedup)를 데모에서도 밟는다.
    for i, (day, part) in enumerate(
            long.with_columns(day=pl.col("tkout_time").dt.date())
                .partition_by("day", as_dict=True, maintain_order=True).items()):
        chunk = R.apply(rf, part.drop("day"))
        p = staging / f"raw_{day[0] if isinstance(day, tuple) else day!s}_{i}.parquet"
        chunk.write_parquet(p)
        files.append(p)

    with dbmod.Store(path) as store:
        rows = dbmod.pivot_and_load(store, files)
    shutil.rmtree(staging, ignore_errors=True)
    log.info("데모 DB 생성: %s (%d행)", path, rows)
    return path


# ── 파일 쓰기 도우미 ─────────────────────────────────────────
def _write_csv(path: Path, df: pl.DataFrame, force: bool,
               b: DemoBundle) -> None:
    if force or not path.exists():
        df.write_csv(path)
        b.made.append(path.name)


def _write_text(path: Path, text: str, force: bool, b: DemoBundle) -> None:
    if force or not path.exists():
        path.write_text(text, encoding="utf-8")
        b.made.append(path.name)


def _write_xlsx(path: Path, sheets: dict[str, pl.DataFrame], force: bool,
                b: DemoBundle) -> Path | None:
    """xlsxwriter로 xlsx를 만든다(없으면 건너뛴다 — csv가 대신한다).

    읽기는 사내 보안상 xlwings만 쓰지만, **쓰기까지 막을 이유는 없다**.
    사내 PC에서 데모를 돌릴 때 엑셀 파일로 열어 보는 흐름이 더 실제에 가깝다.
    """
    if not force and path.exists():
        return path
    try:
        import xlsxwriter

        with xlsxwriter.Workbook(str(path)) as wb:  # polars → xlsxwriter
            for name, df in sheets.items():
                df.write_excel(workbook=wb, worksheet=name, autofit=True,
                               autofilter=False, float_precision=15)
    except Exception as e:                          # noqa: BLE001
        log.info("xlsx를 만들지 못했습니다(%s) — csv를 쓰세요", e)
        path.unlink(missing_ok=True)
        return None
    b.made.append(path.name)
    return path


def guide_text() -> str:
    """번들 폴더에 함께 두는 안내 — 무엇을 눌러 무엇을 확인하는지."""
    return """# ET Report 데모 번들

`etreport --demo`가 만든 시연·점검용 한 벌입니다. 실제 계측 데이터가 아니라
seed를 고정해 만든 합성 데이터라 언제 만들어도 같은 그림이 나옵니다.

## 파일

| 파일 | 무엇 |
|---|---|
| `데모_ET.duckdb` | 분석 화면에서 여는 DB (`et_data`) |
| `데모_리포메터.csv` / `.xlsx` | REAL 13 · ADDP 12 |
| `데모_템플릿.xlsx` | `PLOT`·`TABLE` 두 시트 (한 파일 두 시트) |
| `데모_plot템플릿.csv` · `데모_table템플릿.csv` | Excel 없는 PC용 |
| `데모_실험조건.csv` | lot·wafer × M1 · M5 · M8 |

## 확인할 것

1. **[분석] → [적용](F5)** — 리포메터·템플릿·실험 조건·DB를 한 번에 읽습니다.
   요약에 `REAL 13 · ADDP 12`, 페이지 3장, 표 5개가 뜨면 정상입니다.
2. **탐색 [그리기](Ctrl+Enter)** — `Vtlin N SVT` × `Ioff N SVT`는 두 항목이
   서로 다른 `step_seq`에 기록돼 있습니다. 점이 보이면 §10.1 병합이 살아 있는
   것입니다. y축은 로그(자동 판정), 점을 클릭하면 제외됩니다(Ctrl+Z로 복원).
3. **Mode 콤보** — site / avg / med / std 네 가지로 점 표시를 바꿉니다.
4. **요약 [표 만들기]** — CAT1 5종(DC·Leakage·Ratio·산포·저항·용량)이
   각각 표가 되고, CAT2~CAT4가 표 안 계층입니다. Δ vs REF·규격 이탈 표시,
   복사(TSV)·xlsx 내보내기가 모두 같은 숫자여야 합니다.
5. **그룹 편집** — lot·wafer·step·온도·site 4단 필터로 좁혀 배정해 보세요.
   `PC777`의 `W03`·`W04`는 일부러 미배정으로 뒀습니다.
6. **실험 조건** — factor를 `M1`만 → `M1+M5`로 늘리면 그룹이 늘어납니다.
   `M8`을 함께 고르면 혼입 경고가 뜹니다.
7. **리포트 구성 [미리보기] → PPT 만들기** — plot 페이지 → 표 페이지 →
   제외 이력 순서, 16:9, wafer 25장짜리 lot에서 표 넘침/분할을 봅니다.
8. **리포트 콤보** — `M2_ET` ↔ `DEV_EVAL`을 바꿔 보세요.
9. **[데이터] 화면** — 데모 모드에서는 조회가 가짜 소스로 대체됩니다.
   기간 2026-08-03 ~ 08-05로 추출 → 적재까지 실제 코드로 돕니다.
10. **계측·fab tracking·S3** — 도구 묶음에서 열면 가짜 응답이 옵니다
    (계측 top-k, split 자동 추출, S3 폴더 목록·업로드).
"""
