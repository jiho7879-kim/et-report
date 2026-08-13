"""템플릿 예시 파일 생성 (§11.4).

리포메터·plot·table·실험 조건 4종의 **예시 + '설명' 시트**를 만들어 준다.
처음 쓰는 사람이 "어떤 열을 어떤 값으로 채워야 하는지"를 파일 안에서 바로 볼 수
있어야 하므로, 설명은 별도 문서가 아니라 **같은 파일의 시트**로 넣는다.

Excel이 없거나 저장에 실패하면 **CSV + 설명 CSV로 폴백**한다(확정 사양) — 사내
PC가 아닌 곳에서도 예시는 받아 볼 수 있어야 한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)

DESC_SHEET = "설명"


@dataclass(frozen=True)
class Sample:
    key: str            # 파일 이름에 쓰는 짧은 이름
    title: str          # 메뉴에 보이는 이름
    data: pl.DataFrame  # 예시 표
    desc: pl.DataFrame  # 설명 시트 (컬럼·의미·예시·규칙)


def _desc(rows: list[tuple[str, str, str, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        {"컬럼": [r[0] for r in rows], "의미": [r[1] for r in rows],
         "예시": [r[2] for r in rows], "규칙": [r[3] for r in rows]})


def reformatter_sample() -> Sample:
    data = pl.DataFrame({
        "CATEGORY": ["REAL", "REAL", "REAL", "ADDP", "ADDP"],
        "ITEMID": ["P040_IDSAT_N", "P041_VTLIN_N", "P042_IOFF_N", None, None],
        "ALIAS": ["Idsat N", "Vtlin N", "Ioff N", "Idsat N/P", "Vt spread"],
        "ABSOLUTE": ["N", "N", "TRUE", "N", "N"],
        "SCALE FACTOR": [1.0, 1.0, 1e-6, 1.0, 1.0],
        "ADDP FORM": [None, None, None, "{Idsat N}/{Vtlin N}",
                      "Std({Vtlin N},{Idsat N})"],
        "UNIT": ["uA/um", "V", "A/um", "", "V"],
        "SPECLOW": [500.0, 0.35, None, None, None],
        "SPECHIGH": [900.0, 0.55, 1e-9, None, 0.05],
        "TARGET": [700.0, 0.45, None, None, None],
        "WIDTH": [1.0, 1.0, 1.0, None, None],
        "LENGTH": [0.03, 0.03, 0.03, None, None],
    })
    desc = _desc([
        ("CATEGORY", "REAL(실측) / ADDP(계산)", "REAL",
         "REAL만 DB에서 조회한다. ADDP는 계산 결과라 조회하지 않는다"),
        ("ITEMID", "DB의 실제 item_id", "P040_IDSAT_N", "REAL에만 적는다"),
        ("ALIAS", "툴 안에서 쓰는 이름", "Idsat N",
         "템플릿의 x·y·item_id가 이 값을 가리킨다. 중복되면 마지막 행이 이긴다"),
        ("ABSOLUTE", "절대값 적용 여부", "TRUE",
         "참: TRUE/T/Y/1/O · 거짓: FALSE/N/0/X/빈칸 (대소문자 무관)"),
        ("SCALE FACTOR", "원시값에 곱할 배율", "1e-6", "수식 계산 **전에** 적용된다"),
        ("ADDP FORM", "계산식", "{Idsat N}/{Vtlin N}",
         "함수: ABS SQRT LN LOG LOG10 EXP MIN MAX AVG SUM STD · "
         "LN=자연로그, LOG·LOG10=상용로그 · 참조 대상은 **위쪽 행**에 있어야 한다"),
        ("UNIT", "단위 — 축 이름에 자동으로 붙는다", "uA/um", "비워도 된다"),
        ("SPECLOW/SPECHIGH", "규격", "500 / 900",
         "표의 규격 이탈 표시, plot의 빨간 규격 박스, 축 범위 산정에 쓰인다"),
        ("TARGET", "목표값", "700", "plot에 파란 X로 표시된다"),
        ("WIDTH / LENGTH", "기하 (선택)", "1 / 0.03",
         "trend 차트의 X축으로 쓴다(템플릿 x에는 W·L로 적는다). 없어도 된다"),
    ])
    return Sample("reformatter", "리포메터", data, desc)


def plot_sample() -> Sample:
    data = pl.DataFrame({
        "page": [1, 1, 1, 2, 2],
        "x": ["Vtlin N", "Vtlin N", "Idsat N, Idsat P", "Vtlin N", "W"],
        "y": ["Idsat N", "Ioff N", "Ioff N, Ioff P", "Vt spread",
              "Idsat N, Ioff N"],
        "order": [1, 2, 3, 1, 2],
        "title1": ["NMOS 특성", None, None, "산포·기하", None],
        "title2": ["Idsat–Vt", "Ioff–Vt", "N·P 겹쳐 보기", "Vt spread",
                   "W trend"],
        "Report": ["M2_ET"] * 5,
        "Type": ["scatter", "scatter", "scatter", "scatter", "trend"],
        "x_name": [None, None, None, None, None],
        "y_name": [None, None, None, None, None],
        "Mode": ["site", "site", "avg", "site", "med"],
    })
    desc = _desc([
        ("page", "페이지 번호", "1", "같은 번호끼리 한 슬라이드에 모인다"),
        ("order", "페이지 안 위치", "1~6",
         "1·2·3 윗줄 / 4·5·6 아랫줄, 왼쪽에서 오른쪽"),
        ("x, y", "축에 쓸 리포메터 ALIAS", "Vtlin N",
         "쉼표로 여러 개를 적으면 순서대로 xy쌍이 되어 한 그림에 겹친다"),
        ("title1", "페이지 제목", "NMOS 특성", "같은 page의 첫 행에만 적는다"),
        ("title2", "이 plot의 제목", "Idsat–Vt", ""),
        ("Report", "리포트 이름", "M2_ET",
         "table 템플릿과 짝을 이루는 키. 한 파일에 여러 리포트를 담을 수 있다"),
        ("Type", "그림 종류", "scatter",
         "scatter(기본) / trend(x는 W 또는 L). 표는 전용 페이지로 자동 생성되므로 "
         "table 행을 둘 필요가 없다"),
        ("x_name, y_name", "축 표시 이름", "(비움)",
         "비우면 ALIAS + 단위로 자동 생성"),
        ("Mode", "점을 무엇으로 찍을지 (선택)", "site",
         "site(측정점 그대로) / avg(wafer 평균) / med(wafer 중앙값) / "
         "std(wafer 산포) — 없으면 site. 화면에서도 plot마다 바꿀 수 있다"),
        ("(trend)", "기하 trend 그리기", "Type=trend · x=W",
         "x에 W 또는 L을 적으면 리포메터의 WIDTH·LENGTH 값을 X축으로 쓴다. "
         "탐색 탭에서도 X에 W·L을 넣으면 자동으로 trend가 된다"),
    ])
    return Sample("plot_template", "plot 템플릿", data, desc)


def table_sample() -> Sample:
    data = pl.DataFrame({
        "item_id": ["Idsat N", "Vtlin N", "Ioff N", "Idsat N/P"],
        "CAT1": ["DC", "DC", "Leakage", "Ratio"],
        "CAT2": ["NMOS", "NMOS", "NMOS", "N/P"],
        "CAT3": ["Idsat", "Vt", "Ioff", "Idsat"],
        "CAT4": ["SVT", "SVT", "SVT", "SVT"],
        "Report": ["M2_ET", "M2_ET", "M2_ET", "M2_ET"],
    })
    desc = _desc([
        ("item_id", "리포메터의 **ALIAS**", "Idsat N", "ITEMID가 아니다"),
        ("CAT1", "표를 나누는 기준", "DC",
         "CAT1 하나 = 표 하나 = 시트 하나 = PPT 한 장"),
        ("CAT2, CAT3, CAT4 …", "표 안의 계층", "NMOS",
         "**개수 제한 없음** — CAT5, CAT6을 더 만들어도 번호순으로 인식한다. "
         "상위 CAT이 바뀌면 하위 병합도 끊긴다"),
        ("(행 순서)", "표시 순서", "",
         "시트의 행 순서가 그대로 표의 행 순서가 된다"),
        ("Report", "리포트 이름", "M2_ET", "plot 템플릿과 같은 값으로 맞춘다"),
        ("(표 종류)", "화면에서 고른다", "평균 / 산포 / 그룹별 평균 / 그룹별 wafer",
         "템플릿은 어떤 item을 어떤 계층으로 볼지만 정한다. 값의 종류와 열 구성은 "
         "Summary 탭 콤보에서 고르고, xlsx·PPT가 같은 값을 쓴다"),
    ])
    return Sample("table_template", "table 템플릿", data, desc)


def split_sample() -> Sample:
    data = pl.DataFrame({
        "lot": ["PA1234", "PA1234", "PA1234", "PB5678", "PB5678"],
        "wafer": ["01", "02", "03", "01", "02"],
        "M1": ["Base", "Hi", "Lo", "Base", "Hi"],
        "M5": ["Base", "Base", "Base", "Base", "Base"],
    })
    desc = _desc([
        ("lot", "lot ID", "PA1234", "DB의 lot과 같은 표기로 적는다"),
        ("wafer", "wafer 번호", "01", "슬롯 번호 두 자리 표기를 권장"),
        ("M1, M5, …", "step별 조건 코드", "Base / Hi / Lo",
         "열 이름이 곧 step 이름이다. 비워 두면 Base로 본다"),
        ("(Base)", "기준 조건", "Base",
         "모든 factor가 Base인 조합이 자동으로 REF(회색)가 된다"),
        ("(long 형식)", "다른 입력 방법", "lot | wafer | step_id | code",
         "wide(위 예시) 대신 long 형식으로 적어도 인식한다"),
        ("(붙여넣기)", "파일 없이도 됨", "",
         "엑셀에서 복사해 [실험 조건] 창에 그대로 붙여넣을 수 있다"),
        ("(fab tracking)", "자동으로 채우기", "",
         "[실험 조건] 창의 [fab tracking 자동]은 fab.f_fab_tracking에서 조건이 "
         "갈리는 step만 찾아 이 표를 대신 만들어 준다(PHOTO는 recipe, 그 외 ppid)"),
    ])
    return Sample("split", "실험 조건", data, desc)


def all_samples() -> list[Sample]:
    return [reformatter_sample(), plot_sample(), table_sample(), split_sample()]


# ── 저장 ─────────────────────────────────────────────────────
def save_sample(sample: Sample, out_dir: str | Path) -> Path:
    """xlsx(설명 시트 포함)로 저장. Excel이 없거나 실패하면 CSV로 폴백."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    xlsx = out / f"sample_{sample.key}.xlsx"
    try:
        _write_xlsx(sample, xlsx)
        return xlsx
    except Exception as e:                  # noqa: BLE001 — Excel 없는 PC도 있다
        log.info("xlsx 저장 실패(%s) — CSV로 대신 만듭니다", e)
        return _write_csv(sample, out)


def save_all(out_dir: str | Path, single_file: bool = True) -> list[Path]:
    """4종 저장. 기본은 **한 파일에 시트로 묶어서**(확정 요청).

    엑셀이 있으면 `sample_templates.xlsx` 하나에
    `리포메터 / 리포메터 설명 / plot 템플릿 / plot 템플릿 설명 …` 시트로 넣는다.
    Excel이 없으면 종류별 CSV + `_설명.csv`로 떨어진다(시트가 없으므로).
    """
    out = Path(out_dir)
    if single_file:
        try:
            path = out / "sample_templates.xlsx"
            _write_workbook(all_samples(), path)
            return [path]
        except Exception as e:              # noqa: BLE001 — Excel 없는 PC
            log.info("통합 xlsx 저장 실패(%s) — 종류별 CSV로 만듭니다", e)
    return [save_sample(s, out) for s in all_samples()]


def _write_workbook(samples: list[Sample], path: Path) -> None:
    """4종 + 설명을 한 통합 파일에 시트로. 시트 이름은 31자 제한을 지킨다."""
    import xlwings as xw

    path.parent.mkdir(parents=True, exist_ok=True)
    with xw.App(visible=False, add_book=False) as app:
        wb = app.books.add()
        made = 0
        for smp in samples:
            for name, df in ((smp.title, smp.data),
                             (f"{smp.title} {DESC_SHEET}", smp.desc)):
                sht = wb.sheets.add(name[:31], after=wb.sheets[-1])
                sht["A1"].value = [df.columns, *[list(r) for r in df.rows()]]
                sht.range("A1").expand("right").font.bold = True
                sht.autofit("c")
                made += 1
        if len(wb.sheets) > made:
            wb.sheets[0].delete()           # 기본 빈 시트
        wb.save(str(path))
        wb.close()


def _write_xlsx(sample: Sample, path: Path) -> None:
    import xlwings as xw

    with xw.App(visible=False, add_book=False) as app:
        wb = app.books.add()
        for name, df in ((sample.title, sample.data), (DESC_SHEET, sample.desc)):
            sht = wb.sheets.add(name[:31], after=wb.sheets[-1])
            # 2차원 값은 **모든 행 길이가 같아야** 한다(§10.6) — DataFrame은 안전
            sht["A1"].value = [df.columns, *[list(r) for r in df.rows()]]
            sht.range("A1").expand("right").font.bold = True
            sht.autofit("c")
        if len(wb.sheets) > 2:
            wb.sheets[0].delete()           # 기본 빈 시트
        wb.save(str(path))
        wb.close()


def _write_csv(sample: Sample, out_dir: Path) -> Path:
    """CSV 폴백 — 본문과 설명을 나란히 둔다. BOM을 붙여 엑셀에서 한글이 깨지지 않게."""
    data_path = out_dir / f"sample_{sample.key}.csv"
    desc_path = out_dir / f"sample_{sample.key}_설명.csv"
    for df, p in ((sample.data, data_path), (sample.desc, desc_path)):
        with open(p, "wb") as f:
            f.write(b"\xef\xbb\xbf")
            df.write_csv(f)
    return data_path
