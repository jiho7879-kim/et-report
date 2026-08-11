# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

문서·주석·UI 문자열은 모두 한국어다. 새로 쓰는 것도 한국어로 맞춘다.

## 실행 · 빌드

```bash
myenv/bin/python app.py            # 개발 실행 (빈 상태)
myenv/bin/python app.py --demo     # 샘플 데이터로 UI 확인 (DB·Excel 없이)
myenv/bin/etreport                 # editable 설치된 콘솔 스크립트 (= etreport.app:main)

myenv/bin/python build/build_release.py            # PyInstaller onedir → dist/ETReport + zip
myenv/bin/python build/build_release.py --publish  # 사내 GHE 릴리스 업로드까지 (gh CLI)
```

`myenv/`가 이미 있는 venv(Python 3.11, editable 설치)다. 테스트·린터 설정은
없다 — pytest/ruff 설정도, `tests/` 디렉터리도 존재하지 않는다(일부 docstring이
`tests/ 참조`라고 하지만 실제로는 없음). 동작 확인은 `--demo` 실행이 사실상
유일한 수단이므로, 변경 후에는 데모 모드로 화면이 뜨는지 확인한다.

### 리눅스/WSL에서 못 하는 것
- **Excel 경로 전부** — `xlwings`는 COM(Windows Excel)이 필요하다. 리포메터·
  템플릿·xlsx 내보내기는 `ImportError`로 떨어지고 UI가 "사내 PC에서 실행하세요"를
  띄운다. 이 경로는 코드 리뷰로만 검증 가능.
- **추출** — `bigdataquery`(bdq)는 사내 패키지. 없으면 카탈로그는 `app.py`의
  `_seed_catalog()` 기본 컬럼 목록으로 폴백한다.
- `%APPDATA%`가 없으면 `paths.appdata_dir()`이 `~/ETReport`로 떨어진다.

## 아키텍처 — 큰 그림

두 워크스페이스(`ui/mainwindow.py`의 QStackedWidget)로 나뉜다.

**[데이터] 파이프라인** (`ui/data_ws.py`의 `_ExtractThread`, 백그라운드 QThread):
```
리포메터 로드(xlwings) → querybuilder(Impala SQL) → extractor(일 단위 청크·4워커
→ long parquet) → reformatter.apply(long, 청크별) → db.pivot_and_load(512 버킷
피벗 → DuckDB et_data)
```
단계마다 `log`/`step` 시그널로 초 단위 진행이 화면 하단 로그에 남는다.

**[분석] 파이프라인** (`ui/analysis_ws.py` + `model/session.py`):
파일 선택은 **경로만 담는다(staging)**. [적용]을 눌렀을 때 `session.apply_config()`가
리포메터 → 템플릿 → 실험 조건 → DuckDB 순으로 **한 번만** 읽고 검증하며, 결과를
`LoadReport`(lines/warnings/error) 하나로 모아 UI가 일괄 표시한다. 이 함수는
예외를 던지지 않는다 — 실패도 `LoadReport`로 돌려준다.

### 지켜야 하는 단일 진실(single source of truth)들
이 프로젝트의 설계 대부분은 "같은 규칙을 두 곳에 두지 않는다"에 걸려 있다.
새 코드가 아래를 우회하면 화면과 PPT가 갈라진다.

| 규칙 | 위치 |
|---|---|
| 앱 전역 상태 + 변경 알림 | `model/state.py` (`AppState`, `StateBus` 시그널 5종) |
| 축 범위·로그 판정 | `render/ranges.py` — SPEC∪데이터를 중심 기준 ×1.2 |
| 자릿수 포맷 | `model/specs.py: fmt_value` (<1→3자리, ≤10→2자리, >10→1자리) |
| wafer 집계(평균/n-1 표준편차) | `model/aggregate.py` — 화면·xlsx·PPT 공용, group_by 1회 |
| 그리기 | `render/mpl_renderer.py` — **화면 캔버스도 이걸 쓴다** |

화면은 pyqtgraph가 아니라 matplotlib다. `ui/widgets/plot_canvas.py`가
`mpl_renderer.render(..., fig=self.figure)`로 같은 렌더러에 그린다("화면=PPT"를
검증할 필요를 없애는 대신 줌·팬을 포기한 결정). 일부 docstring에 pyqtgraph가
남아 있는데 이름만 남은 것이다. 점 클릭 제외의 히트테스트 좌표는 **그릴 때
캐시하지 말고 클릭 시점에 변환**해야 한다(슬롯은 그 뒤 크기가 바뀐다).

### 계산은 명시적으로만
표·plot·미리보기는 자동 재계산하지 않는다. Summary [표 만들기] / 탐색 [그리기] /
리포트 [미리보기] 버튼이 트리거이고, 변경이 생기면 버튼이 주황색이 되며 보고 있는
탭만 갱신된다. 새 기능을 넣을 때 이 지연 계산 규약을 깨지 않는다.

## 데이터 계층의 제약

**Excel = xlwings 전용 + 캐시** (`data/xlio.py`). openpyxl/pandas.read_excel은
사내 보안상 쓸 수 없다. Excel 실행은 1~3초짜리 병목이라:
`read_sheets()`로 한 파일의 여러 시트를 Excel **한 번만 띄워** 읽고, 결과를
`%APPDATA%\ETReport\xlcache\*.parquet`에 캐시하며, 원본 mtime+size가 바뀌면 자동
무효화한다. Excel을 읽는 새 코드는 반드시 이 모듈을 경유한다.
`frame_from_rows()`의 열 타입 규칙(전부 숫자/None → Float64, 그 외 → Utf8)은
ADDP FORM 열처럼 위가 비어 있는 열 때문에 필요하다 — 추론으로 바꾸지 말 것.

**DuckDB** — 기준 테이블 이름은 `et_data`(손코딩 시절과 동일, `fact`는 레거시).
분석 화면은 DB를 **읽기 전용**으로 연다. 따라서:
- 컬럼 이름은 고정하지 않고 `data/compat.py`의 `ROLE_ALIASES`로 역할을 추론한다
  (`root_lot_id|lot_id|lot`, `wafer_id|slot_no`, `tkout_time|create_dttm` …).
  long(`item_id`/`value`) 테이블이면 `select_sql()`이 PIVOT으로 wide화한다.
- 제외 포인트는 DB에 쓰지 않고 `data/exclusions.py`가
  `%APPDATA%\ETReport\exclusions\<DB>_<해시>.json` 사이드카에 DB 경로별로 저장한다.
- 분석용 wide 프레임의 예약 컬럼은 `key, lot, wafer, gid`이고 나머지가 item이다
  (`loader.RESERVED`).
- 적재는 `key_hash` ANTI JOIN(행 중복) + `load_log`(파일 중복) 2단으로 막는다.
- 원본 컬럼 `et_value`는 내부 표준 `value`로 정규화한다.

## 리포메터와 템플릿 (Excel 스키마 = 계약)

**리포메터** (`data/reformatter.py`): `CATEGORY ITEMID ALIAS ABSOLUTE
"SCALE FACTOR" "ADDP FORM" UNIT SPECLOW SPECHIGH TARGET`.
처리 순서는 확정 사양 — ① REAL에 SCALE 적용 → ② ABSOLUTE → ③ ADDP를 **시트 행
순서대로**. 아래 행은 위 행의 ADDP를 참조할 수 있고 그 반대는 검증 오류다(행 순서
규칙이 곧 순환참조 차단). 수식은 `eval()`이 아니라 ast 화이트리스트로 파싱하며,
`_compile_expr()`가 polars 식으로 번역해 벡터 계산하고 번역 불가한 것만 행 단위
폴백으로 떨어진다(로그에 남음). `Std(...)`는 표본표준편차(n-1, NULL 제외).
검증 실패 행은 **버리고 나머지로 진행**하며 이유를 `warnings`에 남긴다 — 이게 이
코드베이스 전반의 오류 처리 방식이다(중단하지 않고 건너뛰고 보고).

**템플릿** (`model/templates.py`): plot 시트는 `page x y order title1 title2
Report Type x_name y_name`, table 시트는 `item_id CAT1 CAT2 CAT3 Report`.
`Report` 컬럼이 두 시트의 공통 키 — 한 파일에 여러 리포트를 담고 UI에서 고른다.
`order`는 1~6(윗줄 1·2·3 / 아랫줄 4·5·6). `item_id`와 plot의 x/y는 리포메터
ALIAS여야 하고, 아니면 그 행만 건너뛴다. 리포트 구성 화면의 드래그 결과는
`export/template_writer.py`가 원본 엑셀에 되쓴다(.bak 백업 후 캐시 무효화).

## 릴리스

`src/etreport/__init__.py`의 `__version__` 하나가 태그(`vX.Y.Z`)·zip 이름·업데이트
체커 비교의 기준이다. 릴리스 시 이 값과 `CHANGELOG.md`를 함께 올린다.
(현재 `__version__`/`pyproject.toml`은 1.3.0인데 CHANGELOG는 1.7.3까지 있다 —
다음 릴리스 전에 맞춰야 한다.) 업데이트는 zip을 받아 종료 후 robocopy /MIR로
폴더째 교체한다(`update/apply.py`) — Windows에서 실행 중 exe가 잠기기 때문.

## 사내 식별자 (공개 전 확인 — `PUSH.md`)

`update/checker.py`의 `API_BASE/OWNER/REPO`, `build/build_release.py`의
`--repo pde-tools/et-report`, `data/querybuilder.py`의 테이블 `eds.f_et_test`,
`docs/`의 item alias·step ID가 사내 정보다. `.gitignore`가 `*.duckdb`,
`*.parquet`, `*.xlsx`, `settings.json` 등을 막아 실제 계측 데이터는 커밋되지 않는다.

## 참고 문서

`docs/ui-mockup-v12.html`이 화면 배치·동작의 기준 목업이고,
`docs/data-report-tool-plan.md`가 계획서다(코드 주석의 "계획서 §N" 참조 대상).
`README.md`의 폴더 구조 표는 일부 모듈(session/aggregate/compat/loader/fonts 등)이
빠져 있어 최신이 아니다.
