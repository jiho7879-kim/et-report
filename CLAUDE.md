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

`myenv/`가 이미 있는 venv(Python 3.11, editable 설치)다.

## 테스트 · 린터

```bash
myenv/bin/python -m pytest                          # 기본 (slow 제외, 수 초)
myenv/bin/python -m pytest -m slow -s               # 실측 규모: 20만행/일 · item 1000
myenv/bin/python -m pytest tests/test_reformatter_vector.py -q   # 파일 하나
myenv/bin/python -m pytest -k "addp and not slow"   # 이름으로 고르기
myenv/bin/ruff check .                              # 린트 (설정은 pyproject.toml)
myenv/bin/ruff check --fix .                        # 안전한 것만 자동 수정
```

설정은 `pyproject.toml`의 `[tool.pytest.ini_options]`·`[tool.ruff]`.
`addopts`에 `-m 'not slow'`가 들어 있어 대용량 테스트는 명시할 때만 돈다.
마커는 `slow`(대용량)과 `excel`(사내 PC 전용) 두 개다.

**테스트의 전제**: Excel(xlwings)도 bdq도 없이 전부 돈다. Excel 경로는
`xlio.read_sheet`를 가짜로 바꿔(`fake_sheet` 픽스처) 시트 파싱·검증 로직만
실제 코드로 태운다. 그래서 리눅스/WSL에서도 리포메터·템플릿·적재·분석 로딩을
전부 검증할 수 있다 — 검증 못 하는 건 xlwings COM 호출 자체뿐이다.

| 파일 | 무엇을 고정하나 |
|---|---|
| `tests/factory.py` | 합성 testset 생성기 (실측 규모: item 1000 · 20만행/일) |
| `test_reformatter_load.py` | 시트 스키마 계약, ALIAS 중복·전방참조·수식 화이트리스트 |
| `test_reformatter_formula.py` | 행 단위 엔진의 의미론(NULL·0나눗셈·Std n-1·안전성) |
| `test_reformatter_vector.py` | **벡터 경로 ≡ 행 단위 경로** (여기서 못 잡으면 현장에서 못 잡는다) |
| `test_reformatter_apply.py` | SCALE→ABS→ADDP 순서, ALIAS 개명, 키별 계산 |
| `test_templates.py` | plot/table 템플릿 검증과 Report 분리 |
| `test_pipeline_duckdb.py` | 리포메팅 → 적재 → 읽기전용 로딩 → 제외 사이드카 |
| `test_db_buckets.py` | 버킷 수가 저장 결과를 바꾸지 않는다는 불변식(예전 DB 호환) |
| `test_analysis_core.py` | 축 범위 ×1.2 · 로그 패턴 · wafer 집계 · 자릿수 |
| `test_review_fixes.py` | 코드 리뷰에서 고친 것들의 회귀(업데이트 가드·Figure 누수·연결·복사 값…) |
| `test_ui_smoke.py` | 데모 데이터로 창을 조립(headless) — 탭 구성·지연 계산·복사 일치 |
| `test_bigset.py` (slow) | 실측 규모 성능·정확성 회귀 (`-s`로 단계별 시간 출력) |

`tools/make_testset.py`는 **사내 PC에서 실제 앱으로** 리포메터를 확인하기 위한
testset(리포메터 xlsx + long parquet + 정답표 CSV, `--load`면 DuckDB까지)을 만든다.
정답표는 벡터 경로를 타지 않는 행 단위 엔진으로 계산하므로 앱 결과와 숫자로
대조할 수 있다.

UI는 스모크 수준만 있다(`test_ui_smoke.py`, offscreen). 화면을 바꿨으면
`--demo` 실행으로도 눈으로 확인한다. **headless 테스트에서 모달 창
(`QMessageBox`, `QProgressDialog`)은 영원히 멈춘다** — 그 경로를 테스트하려면
`no_modal_dialogs` 픽스처처럼 반드시 가로채야 한다.

## 리눅스/WSL에서 못 하는 것
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

단일 진실을 건드렸다면 `pytest`가 그 규칙을 지키는지 먼저 확인한다.

화면은 pyqtgraph가 아니라 matplotlib다. `ui/widgets/plot_canvas.py`가
`mpl_renderer.render(..., fig=self.figure)`로 같은 렌더러에 그린다("화면=PPT"를
검증할 필요를 없애는 대신 줌·팬을 포기한 결정). 점 클릭 제외의 히트테스트
좌표는 **그릴 때 캐시하지 말고 클릭 시점에 변환**해야 한다(슬롯은 그 뒤 크기가
바뀐다). 렌더러는 **pyplot을 쓰지 않는다** — `Figure()`를 직접 만든다. pyplot로
만들면 전역 매니저에 등록돼 덱 하나당 수백 개가 남는다.

UI 구조: 도크는 `ui/analysis_ws.py`, 탭 3종은 `ui/tabs/`(explore·summary·report).
지연 계산 토글(버튼 주황색 → 보고 있을 때만 갱신)은 `ui/tabs/common.py`의
`StaleMixin` 하나에 있다 — 탭을 추가하면 여기에 붙인다. 오래 걸리는 작업
(PPT·xlsx)은 `ui/widgets/worker.py`의 `run_in_background`로 넘긴다.

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
  `key_hash_expr()`는 **절대 바꾸지 않는다** — 바뀌면 기존 DB에 이어 적재할 때
  같은 포인트가 중복으로 들어간다.
- 원본 컬럼 `et_value`는 내부 표준 `value`로 정규화한다.
- 적재 버킷 수는 `plan_buckets(키 수, item 수)`가 데이터 모양을 보고 정한다
  (피벗 한 번의 셀 수를 `TARGET_CELLS` 이하로, 상한은 `N_BUCKETS`). 버킷은
  **작업 단위일 뿐 저장 내용과 무관**하므로 예전에 512개로 적재한 DB와 섞여도
  안전하다 — 이 불변식은 `test_db_buckets.py`가 지킨다.

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
