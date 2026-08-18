# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

문서·주석·UI 문자열은 모두 한국어다. 새로 쓰는 것도 한국어로 맞춘다.

## 실행 · 빌드

```bash
myenv/bin/python app.py            # 개발 실행 (빈 상태)
myenv/bin/python app.py --demo     # 데모 — DB·Excel·bdq 없이 모든 기능 확인
myenv/bin/python app.py --demo --demo-rebuild --demo-dir /tmp/etdemo  # 번들 위치·재생성
myenv/bin/python app.py --demo --demo-lite   # 화면만(번들 파일·가짜 소스 없이)
myenv/bin/python tools/make_demo.py --out /tmp/etdemo  # 앱 없이 번들만 만들기
myenv/bin/python app.py --no-update --log-level DEBUG   # 버전 확인 끄기 · 상세 로그
myenv/bin/etreport                 # editable 설치된 콘솔 스크립트 (= etreport.app:main)

myenv/bin/python app.py --run-extract "야간 추출" --days 3   # 창 없이 추출·적재만(§13)

myenv/bin/python build/build_release.py            # PyInstaller onefile → dist/*.exe
myenv/bin/python build/build_release.py --onedir   # 예전 폴더 배포 + zip
myenv/bin/python build/build_release.py --publish  # 사내 GHE 릴리스 업로드까지 (gh CLI)
```

**빌드 정의는 `build/ETReport.spec` 하나다**(빌드 스크립트가 그것을 부른다).
사내에서 인증서·라이브러리를 더 실어야 하면 spec 위쪽의 `SITE_DATAS` /
`SITE_BINARIES` / `SITE_HIDDEN`이나, 커밋되지 않는 `build/site_extras.py`에만
적는다 — spec 본문에 섞으면 다음 갱신에 날아간다.

`myenv/`가 이미 있는 venv(Python 3.11, editable 설치)다.

부팅은 `src/etreport/app.py`가 전부 한다(CLI·로깅 → 폰트·QSS·설정·카탈로그 →
상태·창 → 업데이트 확인). 로그는 콘솔과 `%APPDATA%\ETReport\logs\etreport.log`
(1MB × 3 회전)에 함께 남는다 — **배포 exe는 `--windowed`라 콘솔이 없고
`sys.stderr`가 None이다.** 그래서 부팅에서 StreamHandler를 무조건 붙이지 않고,
처리되지 않은 예외는 `sys.excepthook`이 로그 + 알림 창으로 돌린다(같은 오류는
한 번만, 앱은 죽이지 않는다). 현장 버그는 이 로그 파일이 유일한 단서다.

**패키지와 함께 배포되는 파일은 `etreport/resources.py`로만 찾는다.**
`Path(__file__).with_name(...)`은 exe에서 깨진다 — PyInstaller가 모듈을 압축
아카이브(PYZ)에 넣어서 `__file__` 옆에는 아무것도 없고, `--add-data`로 실은
파일은 `sys._MEIPASS` 아래에 풀린다. style.qss·설명서 PDF·한글 폰트·빌드
스탬프가 전부 이 경로를 거친다(현장에서 "style.qss를 찾지 못했습니다"가 뜬 이유).

**"고쳤는데 exe가 그대로"는 빌드 스탬프로 가른다**(`etreport/buildinfo.py`).
빌드 스크립트가 `assets/build_info.json`(시각·git 해시)을 굽고 번들에 실으며,
부팅 로그 첫 줄과 [도움말] → [버전 · 빌드 정보]가 그 값을 적는다. 값이 예전
그대로면 새 빌드를 실행하고 있지 않은 것이고, 바뀌었는데 동작이 그대로면 그때가
진짜 코드 문제다. spec의 `pathex`가 `src/`를 맨 앞에 두는 것도 같은 목적이다 —
site-packages에 설치된 예전 etreport가 대신 실리는 사고를 막는다.

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
| `test_step_seq_merge.py` | **§10.1** seq 병합·QUALIFY 파티션·합치면 안 되는 키 |
| `test_absolute_reapply.py` | **§10.2** 로딩 시점 절대값 재적용 · 스케일은 재적용 금지 |
| `test_extract_schema.py` | **§10.4** 문자열 시각 파싱 · Categorical · 청크 스키마 고정 |
| `test_pptgen_deck.py` | **§7** 16:9 고정 · 페이지 순서 · 표 전용 페이지 · 병합 순서(§10.5) |
| `test_reformatter_flags.py` | **§3.1** ABSOLUTE 토큰 · LOG(상용)/LN(자연)/EXP |
| `test_group_dialog.py` | **§9.1** 4단 연쇄 필터 · [적용] 없이 조회 · 배정 범위 |
| `test_cat_levels.py` | **§3.3** CAT 개수 자유 — 화면·복사·PPT까지 열이 따라간다 |
| `test_explore_cards.py` | **§5.2** 스케일·범위 카드, 그룹 스타일 카드, 스타일 동기화 |
| `test_manual.py` | 사용 설명서 PDF 내용·생성·메뉴 |
| `test_ux_requests2.py` | 점 표시 모드·PPT 그룹 평균 페이지·그룹별 wafer 표 |
| `test_ux_requests.py` | 사용자 요청 8건(표지 장표·그룹별 평균·콤보 지연…) |
| `test_bugfix_9.py` | 분석 화면 버그 9건 회귀(온도 5단위·자동그룹핑·표 그룹 반영…) |
| `test_ux_requests3.py` | 요청 15건 — 연결 충돌·SQL OOM·S3 트리·wafer 표기 통일·온도 적재 보정 |
| `test_fabtracking.py` | 기능 A — PHOTO=recipe/그 외=ppid, 갈리는 step만 factor |
| `test_metrology.py` | 기능 B — subitem 규칙·(lot,wafer) 매칭·top-k |
| `test_s3.py` | 기능 C — 키 조립·페이지네이션·자격 증명 보관(가짜 클라이언트) |
| `test_chunk_plan.py` | **§4.2·§10.10** 기간×item 그룹 청크 · 진행 라벨 · 미리보기 |
| `test_dock_and_samples.py` | **§5.1·§11.4·§3.4·§14** REPORT 문구·예시 파일·붙여넣기·배정 lot |
| `test_db_buckets.py` | 버킷 수가 저장 결과를 바꾸지 않는다는 불변식(예전 DB 호환) |
| `test_analysis_core.py` | 축 범위 ×1.2 · 로그 패턴 · wafer 집계 · 자릿수 |
| `test_review_fixes.py` | 코드 리뷰에서 고친 것들의 회귀(업데이트 가드·Figure 누수·연결·복사 값…) |
| `test_theme.py` | **시각 토큰 계약** — 치환 누락·WCAG AA 대비·QSS가 덮는 범위 |
| `test_ux_redesign.py` | 단축키(F5·Ctrl+Enter)·상태 레일 램프·미적용 `•`·비모달 알림 |
| `test_hover_states.py` | 버튼 상태 픽셀(기본·dirty·ghost)이 `theme.TOKENS`와 일치 |
| `test_app_boot.py` | 부팅 — 콘솔 없는 exe에서의 로깅, excepthook, 카탈로그 폴백 |
| `test_ui_smoke.py` | 데모 데이터로 창을 조립(headless) — 탭 구성·지연 계산·복사 일치 |
| `test_demo.py` | **데모 계약** — 기능 덮개·번들 파일 == 화면 값·가짜 소스로 추출→적재 |
| `test_xlio_csv.py` | csv·tsv 입력(열 타입 규칙·0으로 시작하는 코드·되쓰기) |
| `test_multi_lot.py` | **§9.2** lot 선택 SQL(안 고르면 예전과 동일)·커버리지·lot 심볼·표 lot 경계·기준 lot |
| `test_requests_13.py` | 요청 13건 — 단일 exe·리소스 경로·빌드 스탬프·fab tracking 이름 컬럼·조회 조건 자동 채움·boxplot·plot 종류·VARCHAR 읽기·Tukey 필터·예약 실행 |
| `test_bigset.py` (slow) | 실측 규모 성능·정확성 회귀 (`-s`로 단계별 시간 출력) |

`tools/make_testset.py`는 **사내 PC에서 실제 앱으로** 리포메터를 확인하기 위한
testset(리포메터 xlsx + long parquet + 정답표 CSV, `--load`면 DuckDB까지)을 만든다.
정답표는 벡터 경로를 타지 않는 행 단위 엔진으로 계산하므로 앱 결과와 숫자로
대조할 수 있다.

UI는 스모크 수준만 있다(`test_ui_smoke.py`, offscreen). 화면을 바꿨으면
`--demo` 실행으로도 눈으로 확인한다(데모는 아래 "데모" 절 참고 — 사내 PC가
아닌 곳에서 기능을 끝까지 밟아 볼 수 있는 유일한 길이다). **headless 테스트에서 모달 창
(`QMessageBox`, `QProgressDialog`)은 영원히 멈춘다** — 그 경로를 테스트하려면
`no_modal_dialogs` 픽스처처럼 반드시 가로채야 한다.

## 리눅스/WSL에서 못 하는 것
- **xlsx 읽기·쓰기** — `xlwings`는 COM(Windows Excel)이 필요하다. xlsx 리포메터·
  템플릿과 xlsx 내보내기는 `ImportError`로 떨어지고 UI가 "사내 PC에서 실행하세요"를
  띄운다. **단 csv·tsv는 읽고 쓸 수 있다**(`xlio`) — 데모 번들이 그 길로 돈다.
- **bdq 조회** — `bigdataquery`는 사내 패키지. 없으면 카탈로그는 `app.py`의
  `_seed_catalog()` 기본 컬럼 목록으로 폴백한다. `--demo`는 조회 입구를 가짜로
  갈아 끼워 추출·계측·fab tracking·S3를 전부 돌려볼 수 있게 한다.
- `%APPDATA%`가 없으면 `paths.appdata_dir()`이 `~/ETReport`로 떨어진다.

## 데모 — 모든 기능을 밟는 한 벌

`--demo`는 "화면이 비지 않게" 채우는 장식이 아니라 **사내 PC 밖에서 기능을
끝까지 확인하는 수단**이다. 네 층으로 나뉘고, 데이터의 출처는 하나다.

| 모듈 | 하는 일 |
|---|---|
| `demo_data.py` | 무엇을 보여 줄지 — 리포메터·템플릿·실험 조건·raw long·계측/tracking |
| `demo_bundle.py` | 그것을 진짜 파일로 — DuckDB·csv·xlsx·`데모_안내.md` |
| `demo_sources.py` | 사내 조회의 **입구만** 가짜로 — `extractor._fetch`·`metrology.fetch`·`fabtracking.fetch`·`s3.client` |
| `demo.py` | 상태에 올리기 — `load_demo()`(in-memory) · `prepare()`(번들+설정+가짜 소스) |

- 데모 DuckDB는 손으로 만들지 않는다 — **리포메팅 → `pivot_and_load`** 실제
  경로로 만든다. 그래서 §10.1 병합·retest·온도 보정·ABSOLUTE 재적용이 데모에서
  진짜로 걸린다. `tests/test_demo.py`가 **DB로 돌아온 값 == 화면 값**을 지킨다.
- 데모 데이터는 함정을 일부러 담는다: DC는 `step_seq=1`·누설은 `2`(병합 없으면
  산점도가 빈다), 음수로 기록되는 PMOS·누설(ABSOLUTE), NULL·0 분모, 25장짜리
  lot(표 넘침), 미배정 wafer, 혼입되는 factor, CAT 4단, 리포트 2종.
- 데모 모드는 **설정을 저장하지 않는다**(`app.py`) — 사용자의 `settings.json`에
  데모 경로가 남으면 다음 실사용에서 엉뚱한 파일을 가리킨다.
- 화면을 바꿨으면 `--demo`로 띄워 보고, 데모 데이터를 바꿨으면
  `tools/make_demo.py --force`로 번들을 다시 만든다.

## 아키텍처 — 큰 그림

두 워크스페이스(`ui/mainwindow.py`의 QStackedWidget)로 나뉜다.

**[데이터] 파이프라인** (`data/pipeline.py: run()`):
```
리포메터 로드(xlwings) → querybuilder(Impala SQL) → extractor(일 단위 청크·4워커
→ long parquet) → reformatter.apply(long, 청크별) → db.pivot_and_load(512 버킷
피벗 → DuckDB et_data)
```
**이 함수는 Qt를 모른다.** `ui/data_ws.py`의 `_ExtractThread`는 콜백을 시그널로
옮기는 껍데기일 뿐이고, 예약 실행(§13)도 같은 함수를 그냥 부른다 — 화면 안에
두면 두 경로가 조용히 갈린다. 단계마다 `log`/`step`으로 초 단위 진행이 화면
하단 로그에 남는다.

**예약 실행**(`etreport/schedule.py`, 요청 §13): `ETReport.exe --run-extract
"<프리셋>" --days N`이 창 없이 추출·적재만 하고 **종료 코드로** 결과를 알린다
(0=성공 / 1=실패 / 2=프리셋 없음). 그 실행을 반복하는 일은 Windows 작업
스케줄러(`schtasks`)에 맡긴다 — 상주 프로세스는 로그아웃하면 죽고 죽은 것을
아무도 모른다. 작업은 `/IT`(로그인 사용자로만)로 등록한다: bdq 자격과 Excel
COM이 계정에 묶여 있어 로그인 없이 돌리면 조용히 빈 결과가 적재된다.

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
| 앱 전역 상태 + 변경 알림 | `model/state.py` (`AppState`, `StateBus` 시그널 6종) |
| 색·모서리·글자 크기 | `ui/theme.py: TOKENS` — `style.qss`의 `%TOKEN%`으로만 들어간다 |
| 축 범위·로그 판정 | `render/ranges.py` — SPEC∪데이터를 중심 기준 ×1.2 (로그 축이면 ×1.2도 로그 공간에서) |
| boxplot x축 후보·값 | `model/categories.py` — `lot+wafer`(가상)·lot·wafer·gid·step·temp·site + tracking/계측 컬럼 |
| plot 종류 목록 | `model/specs.py: PLOT_TYPES` — 화면 콤보와 템플릿 `Type` 열이 같은 목록을 본다 |
| 그림·표에서 뺄 점 | `model/state.py: AppState.hidden()` = 손으로 찍은 제외 ∪ 이상치 필터 |
| 이상치 판정 | `model/outliers.py` — Q1−k·IQR / Q3+k·IQR, 기본은 `(step, temp)`별 |
| 조회 조건 기본값 | `data/lotcontext.py` — 분석 DB의 line·process·part + ET tkout 기준 180일 |
| 자릿수 포맷 | `model/specs.py: fmt_value` (<1→3자리, ≤10→2자리, >10→1자리) |
| wafer 집계(평균/n-1 표준편차) | `model/aggregate.py` — 화면·xlsx·PPT 공용, group_by 1회 |
| lot·wafer 표기 비교 | `model/wafers.py` — `W01`·`W1`·`01`·`1`을 한 키로 |
| 그리기 | `render/mpl_renderer.py` — **화면 캔버스도 이걸 쓴다** |
| 덱 조립 | `render/pptgen.py: build_deck` — 크기·순서·표 페이지가 전부 여기 |

단일 진실을 건드렸다면 `pytest`가 그 규칙을 지키는지 먼저 확인한다.

**PPT 규칙 세 가지**(`render/pptgen.py`, `tests/test_pptgen_deck.py`가 지킨다):
슬라이드는 **항상 16:9**(13.333 × 7.5) — 크기는 프레젠테이션 전역이라 표 때문에
넓히면 plot 페이지까지 늘어진다. 페이지 순서는 `plot 전부 → 표 전부 → 제외 이력`
이고, **표는 실험(factor)과 무관하므로 한 벌만** 만든다(CAT1마다 한 장, 전용
페이지 — plot 템플릿의 `Type=table` 행은 무시한다). wafer가 많으면 글자를 9pt
아래로 줄이지 않고 `overflow`(표가 슬라이드 밖으로 이어짐) 또는 `split`(12장씩
분할)로 처리한다. 표 셀은 **반드시 병합 먼저, 값 나중** — 반대로 하면 python-pptx가
텍스트를 이어붙여 lot 헤더가 `PA1\nPA1\nPA1`이 된다.

화면은 pyqtgraph가 아니라 matplotlib다. `ui/widgets/plot_canvas.py`가
`mpl_renderer.render(..., fig=self.figure)`로 같은 렌더러에 그린다("화면=PPT"를
검증할 필요를 없애는 대신 줌·팬을 포기한 결정). 점 클릭 제외의 히트테스트
좌표는 **그릴 때 캐시하지 말고 클릭 시점에 변환**해야 한다(슬롯은 그 뒤 크기가
바뀐다). 렌더러는 **pyplot을 쓰지 않는다** — `Figure()`를 직접 만든다. pyplot로
만들면 전역 매니저에 등록돼 덱 하나당 수백 개가 남는다.

**REF μ±3σ 밴드는 넣지 않는다.** 사양서 §6·§5.2에 남아 있지만 2026-08-11에
**전체 plot에서 삭제하기로 확정**됐고(`docs/trend-chart-plan.md` §2-4),
`test_render_trend.py`의 `test_ref_band_field_removed`·
`test_renderer_module_has_no_ref_band`가 재발을 막는다. 사양서만 보고 되살리지 말 것.

**화면 테마는 `ui/theme.py`가 전부 갖는다**(2026-08-15 리디자인, "계측기 콘솔").
상단바·도크는 무채색 그래파이트(크롬), 카드·표·캔버스는 흰색(측정면)이고 액센트는
딥 틸 하나다 — 차트가 이미 Okabe-Ito 8색과 규격 빨강을 쓰므로 UI가 같은 색조로
경쟁하지 않게 한 결정이다. 지켜야 할 것 셋:
- 색·모서리·글자 크기는 **`TOKENS`에만** 적는다. `style.qss`는 `%TOKEN%`을 쓰고
  파이썬 코드에 hex를 박지 않는다(`tests/test_theme.py`가 대비를 이 표로 검사한다).
- **전역 `QWidget { background: … }` 규칙을 두지 않는다.** 그 규칙이 있으면 어두운
  도크 안의 자식 위젯이 전부 밝은 회색으로 칠해진다. 배경은 QPalette와 이름 있는
  표면(`#topbar` `#dock` `#card` …)에만 준다.
- 스타일은 **Fusion 고정**(`theme._use_fusion`). Windows 기본 스타일은 스크롤바·
  체크박스·콤보 화살표를 자기 식으로 그려서 QSS로 칠한 나머지와 따로 논다.
- 콤보 화살표·체크 표시 아이콘은 토큰 색으로 **부팅 때 만들어** `%APPDATA%\\ETReport\\
  icons`에 둔다(`theme.icon_dir`). 파일로 들고 다니면 색이 두 곳이 된다.

**콤보 처리는 `ui/tabs/common.on_combo()`로 연결한다** — 선택 즉시 팝업을 닫고
(hidePopup) 60ms 뒤에 실행한다. 핸들러에서 바로 무거운 일을 하면 팝업이 화면에
남는다(실제로 두 번 재발했다). 창이 닫힌 뒤 도는 지연 처리는 조용히 건너뛴다.

**사용 설명서**: `tools/make_manual.py`가 데모를 offscreen으로 띄워 캡처하고
`export/manual.py`(QPdfWriter)가 PDF로 묶는다. 산출물은
`assets/manual/ET_Report_사용설명서.pdf`이고 [도움말] 메뉴가 연다. 화면을 바꿨으면
이 스크립트를 다시 돌려 설명서를 갱신한다.

UI 구조: 도크는 `ui/analysis_ws.py`, 탭 3종은 `ui/tabs/`(explore·summary·report).
지연 계산 토글(버튼 주황색 → 보고 있을 때만 갱신)은 `ui/tabs/common.py`의
`StaleMixin` 하나에 있다 — 탭을 추가하면 여기에 붙인다. 오래 걸리는 작업
(PPT·xlsx·[적용]·SQL 조회/저장)은 `ui/widgets/worker.py`의 `run_in_background`로
넘긴다. **단 matplotlib 렌더는 워커에서 돌리지 않는다** — 폰트·텍스트 메트릭
캐시가 스레드 안전하지 않아 QThread에서 그리면 프로세스가 abort한다(확인함).
[그리기]/[미리보기]는 UI 스레드에서 그리되 버튼 잠금 + 대기 커서로 표시한다.

단축키는 창 전역(`MainWindow._build_shortcuts`: Ctrl+1·Ctrl+2·F1)과 화면별
(`AnalysisWorkspace`: F5·Ctrl+Enter·Ctrl+Z / `DataWorkspace`: F5·Esc)로 나뉜다.
Ctrl+Enter는 보고 있는 탭의 `stale_button_attr` 버튼을 누른다 — 탭을 추가해도
그 속성만 정의하면 따라온다. 새 단축키를 넣으면 [도움말] → [단축키]와 설명서의
단축키 절도 함께 고친다.

확인만 받는 알림(캐시 비움·예시 저장 등)은 모달이 아니라
`ui/widgets/toast.py`의 `toast()`를 쓴다. 모달은 실패와 되돌릴 수 없는 확인에만.

### boxplot과 plot 종류

plot 종류는 `scatter · box · trend` 셋이고 목록은 `model/specs.PLOT_TYPES`
하나다 — 템플릿의 `Type` 열, 탐색 탭 [종류] 콤보, 리포트 슬롯 인스펙터가 같은
목록을 본다(갈리면 템플릿으로 저장했다 다시 열 때 종류가 바뀐다).

**boxplot의 x는 item이 아니라 범주다.** 무엇을 범주로 쓸 수 있는지와 값 만드는
법은 `model/categories.py`가 독점한다 — `lot+wafer`는 DB에 없는 **가상 컬럼**
이라 그릴 때 만들고(적재해 두면 lot·wafer 표기 규칙이 두 곳이 된다), `gid`는
축에 gid가 아니라 **그룹 이름**으로 적힌다. 숫자 컬럼은 후보에서 뺀다(값마다
상자가 하나씩 생긴다) — `choices()`와 `is_category()`가 같은 기준을 써야 한다.
상자는 범주 자리마다 **그 자리에 값이 있는 그룹끼리만** 폭을 나눈다: 전체 그룹
수로 나누면 그룹과 범주가 1:1일 때 상자가 눈금에서 비켜 그려진다.

### 이상치 필터 (Tukey)

표·plot을 그리기 **전에** `lo = Q1 − k·IQR`, `hi = Q3 + k·IQR` 밖을 걸러 낸다.
`k`는 사용자가 정하고(프리셋 3.0·4.5) 기본은 꺼짐 — 데이터를 버리는 동작은
사용자가 켜야 시작된다. 사분위수는 **`(step, temp)`마다 따로** 구한다: 25 ℃와
125 ℃를 합쳐 세면 정상적인 고온 측정이 통째로 이상치가 된다. 표본이
`MIN_POINTS`(12) 미만인 묶음은 경계를 NULL로 두어 아무것도 거르지 않는다.

걸러진 점은 **버리지 않고 기록한다** — `state.filtered`에 남고
`data/exclusions.py`의 **별도 사이드카**(`*.filter.json`)에 저장되며, 화면에는
회색 빈 심볼로 그대로 보인다. 손으로 찍은 제외와 파일을 나눈 이유는 필터를 끌 때
사람이 뺀 점까지 지우지 않기 위해서다. **읽는 쪽은 전부 `state.hidden()`을
쓴다** — `excluded`만 보는 코드가 하나라도 남으면 그 화면에서만 필터가 빠져
화면과 PPT의 숫자가 갈린다.

### 계산은 명시적으로만
표·plot·미리보기는 자동 재계산하지 않는다. Summary [표 만들기] / 탐색 [그리기] /
리포트 [미리보기] 버튼이 트리거이고, 변경이 생기면 버튼이 앰버색 + 라벨 끝에 `•`가
되며(색만으로 알리지 않는다 — `tabs/common.set_dirty`) 보고 있는 탭만 갱신된다.
새 기능을 넣을 때 이 지연 계산 규약을 깨지 않는다.

## 데이터 계층의 제약

**Excel = xlwings 전용 + 캐시** (`data/xlio.py`). openpyxl/pandas.read_excel은
사내 보안상 쓸 수 없다. Excel 실행은 1~3초짜리 병목이라:
`read_sheets()`로 한 파일의 여러 시트를 Excel **한 번만 띄워** 읽고, 결과를
`%APPDATA%\ETReport\xlcache\*.parquet`에 캐시하며, 원본 mtime+size가 바뀌면 자동
무효화한다. Excel을 읽는 새 코드는 반드시 이 모듈을 경유한다.
`frame_from_rows()`의 열 타입 규칙(전부 숫자/None → Float64, 그 외 → Utf8)은
ADDP FORM 열처럼 위가 비어 있는 열 때문에 필요하다 — 추론으로 바꾸지 말 것.
**csv·tsv는 Excel 없이 같은 모양으로 읽는다**(`read_text_table`, 시트 인자는
무시). 폴백이 아니라 정식 입력이다 — 예시 파일(§11.4)이 Excel 없는 PC에서
CSV로 떨어지는데 그걸 다시 읽을 길이 없었고, 데모 번들도 이 길로 돈다.
셀 해석은 `_text_cell` 하나에 있다: 빈 칸은 NULL, 숫자처럼 보이면 숫자, 단
**앞이 0인 코드(`0012`)는 문자열로 둔다**(숫자로 보면 `12`가 되어 뭉갠다).

**추출 청크** (`data/extractor.py`) — 조회는 `item_id IN (...)`으로 반드시 좁히고
(리포메터 REAL의 ITEMID만), 청크는 **기간 × item 그룹의 곱**이다(`plan_units`).
item은 9999개씩 나눠 쿼리 하나가 Impala IN 상한을 넘지 않게 한다. 그 곱 전체가
병렬 대상이며(bdq는 스레드 안전) 진행 라벨은 `08-04 item 2/3` 형태다. 화면의 SQL
미리보기는 `build_preview_sql()`로 **개수 주석만** 만든다 — 목록을 문자열로 펴면
24,180개 기준 43만 자가 되어 조건을 고칠 때마다 다시 그린다.

**추출 결과 정규화** (`data/extractor.py: normalize_schema`) — bdq 결과는 반드시
여기를 거쳐 long 고정 스키마가 된다. `cast` 하나로 끝내면 안 되는 이유가 둘 있다:
Categorical → 숫자 직접 캐스팅은 polars가 막고(Utf8을 한 번 거친다), **문자열 →
Datetime은 cast가 조용히 전부 null로 만든다**(`str.to_datetime()`으로 파싱).
tkout_time이 null이 되면 `key_hash`가 뭉쳐 서로 다른 측정이 중복으로 지워진다.
빠진 컬럼은 null로 채워 청크 parquet 스키마를 고정한다(`scan_parquet` 일괄 읽기).
여기서 **온도도 5단위로 보정한다**(`correct_temperature`) — 리포메팅·적재가
전부 보정된 값으로 진행되고 DuckDB에도 보정된 값이 들어간다. 반올림은 DuckDB
`ROUND`와 같은 규칙(0.5는 0에서 먼 쪽)이라 읽는 시점 보정(`compat.temp_expr`)과
값이 같고, 반올림은 멱등이라 raw로 적재해 둔 예전 DB도 그대로 맞는다.

**DuckDB** — 기준 테이블 이름은 `et_data`(손코딩 시절과 동일, `fact`는 레거시).
분석 화면은 DB를 **읽기 전용**으로 연다. **읽기 전용으로 여는 자리는 전부
`loader.open_readonly()`를 쓴다** — DuckDB는 같은 파일에 설정이 다른 연결을
동시에 열지 못해서(`can't open a connection to same database file with a
different configuration`), `duckdb.connect(..., read_only=True)`를 직접 부르는
코드가 하나만 생겨도 그 순간 충돌한다. 쓰기(적재)와 읽기도 함께 열 수 없으므로
추출을 시작하기 전에 `loader.close_store(state)`로 읽기 연결을 닫는다. 따라서:
- 컬럼 이름은 고정하지 않고 `data/compat.py`의 `ROLE_ALIASES`로 역할을 추론한다
  (`root_lot_id|lot_id|lot`, `wafer_id|slot_no`, `tkout_time|create_dttm` …).
  long(`item_id`/`value`) 테이블이면 `select_sql()`이 PIVOT으로 wide화한다.
- **타입 때문에 읽기가 막히지 않는다**(요청 §8). 예전에 손으로 만든 DB는
  temperature가 VARCHAR인 일이 있는데, 문자열에 산술을 걸면 DuckDB가 값 하나
  (`'n/a'`·빈칸) 때문에 조회 전체를 떨어뜨려 **DB가 통째로 안 열렸다**. 그래서
  `temp_expr()`는 `TRY_CAST`를 거치고(못 읽는 값만 NULL, 숫자 컬럼에서는 결과가
  예전과 같다), 숫자 item이 **하나도 없는** 테이블은 남은 문자열 컬럼을 item으로
  보고 `TableProfile.numeric_expr()`로 읽는다. 적재는 여전히 숫자만 받는다 —
  읽기만 관대해진다.
- **`step_seq`만 다른 행은 읽으면서 한 측정점으로 합친다**(`compat.merges_seq`·
  `MERGE_ROLES`). x가 `step_seq=1`·y가 `2`에 기록되는 경우가 흔한데, 합치지
  않으면 x·y가 함께 있는 행이 0개가 되어 산점도가 통째로 빈다. 그룹 키는
  `lot·wafer + die 좌표·온도·step_id·site_cnt`이고 각 item은 `any_value`(NULL이
  아닌 값). **step_id·온도·site_cnt가 다르면 다른 측정점이므로 합치지 않는다.**
  retest QUALIFY 파티션에는 반드시 `step_seq`를 포함한다 — 빼면 seq가 다른
  정상 행이 '구버전'으로 지워진다. seq 컬럼이 없는 스키마는 병합하지 않는다
  (키가 부족한 채로 그룹핑하면 wafer 하나가 한 점으로 뭉갠다).
  합친 행의 `key`는 구성 행 key의 최솟값 — seq가 하나뿐인 DB에서는 예전 값과
  같아서 제외 사이드카가 그대로 유지된다. 규칙은 `tests/test_step_seq_merge.py`.
- 제외 포인트는 DB에 쓰지 않고 `data/exclusions.py`가
  `%APPDATA%\ETReport\exclusions\<DB>_<해시>.json` 사이드카에 DB 경로별로 저장한다.
- 분석용 wide 프레임의 예약 컬럼은 `key, lot, wafer, gid, step, temp, site`이고
  나머지가 item이다(`loader.RESERVED` — 컬럼 목록을 손으로 적지 말고
  `loader.item_columns()`를 쓸 것). `step/temp/site`는 측정 조건이며 그룹 편집의
  4단 필터와 배정 범위가 쓴다. 없는 스키마에서도 NULL로 자리를 만든다.
- 손으로 짠 그룹은 `state.manual_groups`(`(lot, wafer, step, temp, site) → gid`,
  None은 조건 무관)에 남고 `loader.apply_manual_groups()`가 로딩 때 다시 붙인다 —
  [적용]으로 DB를 다시 읽어도 배정이 살아 있고, **[적용] 전에 짜 둔 그룹도**
  그대로 반영된다. 실험 조건 배정보다 뒤에 걸어 사용자가 고른 쪽이 이긴다.
- 적재는 `key_hash` ANTI JOIN(행 중복) + `load_log`(파일 중복) 2단으로 막는다.
  `key_hash_expr()`는 **절대 바꾸지 않는다** — 바뀌면 기존 DB에 이어 적재할 때
  같은 포인트가 중복으로 들어간다.
- 원본 컬럼 `et_value`는 내부 표준 `value`로 정규화한다.
- **ABSOLUTE는 로딩할 때 다시 건다**(`loader.apply_absolute`). 추출 시점에만
  적용하면 이미 음수로 적재된 DB는 리포메터를 고쳐도 그대로다. 절대값은
  멱등이라 안전하지만 **스케일은 멱등이 아니므로 여기서 절대 재적용하지 않는다.**
- 적재 버킷 수는 `plan_buckets(키 수, item 수)`가 데이터 모양을 보고 정한다
  (피벗 한 번의 셀 수를 `TARGET_CELLS` 이하로, 상한은 `N_BUCKETS`). 버킷은
  **작업 단위일 뿐 저장 내용과 무관**하므로 예전에 512개로 적재한 DB와 섞여도
  안전하다 — 이 불변식은 `test_db_buckets.py`가 지킨다.

## 멀티 lot (§9.2)

한 DB에 lot이 여럿 들어 있을 때의 규칙이다. 근거와 결정 과정은
`design-plans/multi-lot-analysis.md`에 있다.

- **읽을 lot은 SQL에서 좁힌다** — 도크 [lot] 절에서 체크한 lot이
  `state.lots_selected`에 담기고 `compat.select_sql(prof, lots=…)`가
  `WHERE lot IN (…)`을 건다. **`lots`가 비면 예전과 글자 하나까지 같은 SQL**이어야
  한다(안 그러면 지금까지 정상 동작하던 DB의 동작이 조용히 바뀐다). WHERE는
  retest QUALIFY보다 **앞**이다. 빈 리스트 = 전부.
- lot 선택은 설정 프리셋이 아니라 **DB 경로별**로 `Settings.lot_selections`에
  남는다. 체크를 바꾸면 [적용]이 dirty가 될 뿐 **즉시 다시 읽지 않는다**(지연 계산).
- **전부 고른 상태는 빈 리스트로 둔다**(설정에도 기록하지 않는다). 그래야 SQL이
  예전과 똑같고, 이 화면을 띄운 뒤 적재로 lot이 늘어도 그 lot이 조용히 빠지지
  않는다 — 목록에 없던 lot을 IN에 적을 수는 없기 때문이다.
- 커버리지 줄은 `LoadReport.warnings`가 아니라 **`notes`**로 간다. 버린 것이
  없는데 "제외 N건"으로 세면 안 되고, 볼 때마다 모달이 뜨면 안 된다 — 알림은
  토스트, 자세한 것은 [커버리지] 다이얼로그다.
- 고른 lot은 그룹 편집·inline 계측·fab tracking 조회에 전파된다. **SQL 조회 창은
  예외** — 사용자가 쓴 SQL을 그대로 돌려야 한다.
- **커버리지 판정은 `model/coverage.py` 하나에만 있다** — 기준 lot은 item이 가장
  많은 lot(동률이면 이름 순), 결손은 ①값이 전부 NULL ②측정 조건 조합 불일치
  ③wafer당 포인트 **중앙값**이 기준의 ±20%(`POINT_TOLERANCE`) 밖. [적용] 로그 줄과
  [커버리지] 다이얼로그가 같은 함수를 쓴다.
- **lot 심볼 분화는 렌더러 안에서만** 한다(`mpl_renderer.lot_markers`·`_lot_parts`).
  `{gid: DataFrame}`를 만드는 곳이 화면(`plot_canvas.render_args`)과
  PPT(`deckbuild._plot_data`) 둘이라, 거기서 쪼개면 "화면 = PPT"가 깨진다.
  marker는 `data` **전체**를 보고 한 번에 정한다 — 그래야 같은 lot이 모든 그룹·
  모든 페이지에서 같은 모양이다. 토글이 켜진 동안 그룹의 `symbol`은 무시된다.
- PPT 표는 **split 모드일 때만** lot 경계로 먼저 끊고, 한 lot이 12장을 넘으면 그
  안에서 다시 끊는다(`pptgen.split_table`). overflow 모드와 화면·xlsx 표는 그대로다.
  머리글 블록 경계로 끊는 것이므로 그룹 머리글(gwafer)에도 같은 규칙이 걸린다.
  **장수는 늘어난다** — 데모(lot 4개·41열) 기준 표 슬라이드가 25 → 35장이다.
  그 대신 lot 머리글이 슬라이드 경계에서 잘리지 않는다.
- **`SplitMatrix.baseline`은 코드 하나가 아닐 수 있다.** 기준 lot을 고르면
  step마다 그 lot의 다수 조건이 기준이 되어 `baseline_codes`(step→코드)에 담긴다.
  REF 판정은 `code_of(step)`을 쓴다. 맵이 비어 있으면 예전처럼 `baseline` 하나를
  전 step에 쓴다 — 예전 설정·파일 호환이 여기에 달려 있다.
- 자동 그룹핑 `split factor별`은 실험 조건의 배정을 **`manual_groups`로 굽는다**.
  도크 [factor 편집]은 gid를 직접 써서 [적용] 때 되돌아가는데, 구워 두면
  `apply_manual_groups`가 실험 조건보다 뒤에 걸려 손으로 고친 쪽이 이긴다.
- 자동 그룹핑에 lot을 주면(`auto_group(mode, lots)`) **그 lot의 배정만** 다시
  만든다(`_clear_scope`) — 다른 lot에 짜 둔 그룹까지 날리면 lot을 갈아 가며
  작업할 수 없다. 지울 때는 `manual_groups`와 **프레임의 `gid`를 함께** 비운다.
  다시 배정받지 못한 wafer가 예전 gid를 달고 남으면 안 되기 때문이다.
  lot을 주지 않으면(단일 lot 탭) 지금까지처럼 전부 다시 만든다.
- 계측 `top_factors`는 합친 상관 `r`과 **lot 내 상관 `r_within`**을 함께 낸다.
  정렬 점수는 둘 중 **보수적인 쪽** — lot 평균 차이만으로 생긴 상관("lot 효과")이
  위로 올라오지 않게 한다. lot이 하나면 `r_within`은 None이라 순위가 예전과 같다.

## 사내 소스 3종 (ET 계측 외)

**fab tracking** (`data/fabtracking.py`) — `fab.f_fab_tracking`에서 split 실험
lot을 찾는다. **`area='PHOTO'`면 recipe(`reticle_id`), 그 외는 `ppid`**로 step별
조건을 비교하고, **조건이 갈리는 step만** 실험 축(factor)으로 올린다. 그룹핑과
혼입 감지는 `model/split.SplitMatrix`가 하며 여기서 매트릭스만 만들어 넘긴다 —
그룹핑 로직을 두 곳에 두지 않는다.

**뽑을 컬럼의 이름은 사용자가 정한다**(`TrackColumn`, 요청 §2). 예전에는
`process_id` 값이 그대로 표의 머리글이 돼서 `1400` 같은 코드가 축 이름으로
나갔고, 한 step에서 recipe와 설비를 함께 볼 수도 없었다. 이제 **이름 · 원본
컬럼 · step**을 줄마다 정하고(`derive()`), `attach()`가 (lot, wafer)로 분석
프레임에 붙인다 — 그때부터 boxplot x축·표 범주·PPT 슬라이드에서 쓰인다
(`state.track_columns` / `state.track_frame`). 기본 제안(`suggest_columns()`)은
조건이 갈리는 step 그대로라 **창을 열자마자 보이는 것은 지금까지와 같은 결과**다.
계측과 달리 같은 이름이 있으면 **덮어쓴다** — 조건을 고쳐 다시 뽑는 흐름이라
덮지 않으면 고친 결과가 반영되지 않는다.

**inline 계측** (`data/metrology.py`) — `fab.f_fab_wf_met`. 조회는 **분석 중인
lot으로 반드시 좁힌다**(전체 스캔 금지). subitem 규칙은 확정 사항이다: site
level은 `RANGE/STD/MIN/VALUE/SLOTID/Q2/MAX`를 **뺀** 나머지, wafer level은 `Q2`.

**두 조회 모두 조건을 분석 DB에서 채우고 SQL을 직접 고칠 수 있다**(요청 §3).
line·process·part와 기간 기본값은 `data/lotcontext.py`가 DuckDB에서 읽어 온다 —
기간은 **ET tkout_time 기준 180일 이전부터 tkout_time까지**(계측·tracking은 ET
보다 앞선 공정에서 찍히므로 그 뒤를 볼 이유가 없다). 손으로 적으면 오타 하나에
조회가 비고, 그때는 "데이터가 없는 것"과 구별되지 않는다. 창이 SQL을 다시
만드는 것은 [조건으로 다시 만들기]를 눌렀을 때뿐이다 — 고쳐 둔 SQL을 조용히
덮지 않는다.
계측 열 이름은 `step_id::item_id`(다른 step의 같은 item이 뭉치지 않게).
`top_factors()`는 wafer 집계를 `model/aggregate`에서 가져와 상관(r)·그룹 차이
(Welch t)로 순위를 매기고, 결과는 `state.met_top` → PPT 슬라이드로 나간다.

**S3** (`data/s3.py`) — boto3는 **선택 의존성**(`pip install -e ".[s3]"`).
자격 증명은 `settings.json`이 아니라 `%APPDATA%\ETReport\s3_credentials.json`
(POSIX 0600)에 두고, 저장 여부는 사용자가 고른다. 폴더 목록은 Delimiter로 한
단계씩만 읽는다(큰 버킷을 재귀로 훑지 않는다).

세 소스 모두 bdq/boto3가 없는 리눅스에서 **가짜 프레임·가짜 클라이언트**로
테스트한다 — 컬럼명은 원본 그대로 유지하고 축약하지 않는다.

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

**ADDP는 step_seq를 넘나들 수 없다.** 리포메팅은 추출 직후, 즉 §10.1 병합보다
**앞에서** 돌기 때문에 seq가 갈려 기록되는 두 항목(예: DC는 seq 1, 누설은 2)을
한 수식에 쓰면 한 행에 함께 있는 적이 없어 결과가 전부 NULL이고, `apply()`
끝의 `drop_nulls`가 그 item을 통째로 지운다. 데모 리포메터의 수식이 seq 안에서만
참조하는 이유다 — 이 제약을 모르고 수식을 짜면 "만든 ADDP가 사라진다".

함수 목록은 확정 사양이다(`_BASE_FUNCS`): `ABS SQRT LN LOG LOG10 EXP MIN MAX AVG
SUM STD`. **`LN`은 자연로그(밑 e), `LOG`·`LOG10`은 상용로그(밑 10)** — 엑셀 관례를
따르며 헷갈리기 쉬우니 바꾸지 말 것. 함수를 추가하면 `_VEC_UNARY`/`_VEC_NARY`에도
**같은 의미로** 넣어야 한다(`test_reformatter_vector.py`가 화이트리스트를 훑어
벡터 경로가 빠지면 실패시킨다). polars는 0으로 나눠도 예외 대신 ±inf를 주므로
나눗셈·거듭제곱 결과는 `_finite()`로 즉시 NULL 처리한다 — 최종 결과만 걸러 내면
`Exp({A}/{B})`처럼 inf가 함수를 거쳐 멀쩡한 값(exp(-inf)=0)으로 둔갑한다.

`ABSOLUTE` 같은 참/거짓 셀은 `parse_flag()` 하나로 읽는다 — **`TRUE/T/Y/1/O`가
참, `FALSE/N/0/X/빈칸`이 거짓**(대소문자 무관, 엑셀 체크박스의 bool과 1.0/0.0도
처리). 모르는 값은 거짓으로 두되 경고를 남긴다. 예전처럼 `== "Y"`로 비교하면
`TRUE`로 적은 행의 절대값이 조용히 무시된다.

**템플릿** (`model/templates.py`): plot 시트는 `page x y order title1 title2
Report Type x_name y_name`, table 시트는 `item_id CAT1 … Report`.
`Report` 컬럼이 두 시트의 공통 키 — 한 파일에 여러 리포트를 담고 UI에서 고른다.
`order`는 1~6(윗줄 1·2·3 / 아랫줄 4·5·6). `item_id`와 plot의 x/y는 리포메터
ALIAS여야 하고, 아니면 그 행만 건너뛴다.

**CAT 개수는 고정하지 않는다**(§3.3 확정). `templates.cat_columns()`가 정규식
`CAT\d+`로 찾아 **번호순**으로 정렬하므로 CAT4·CAT5를 더 두면 그만큼 계층이
늘어난다. 필수 컬럼은 `TBL_REQUIRED`(`item_id CAT1 Report`)뿐이다 — CAT3이
없다고 중단하면 안 된다. 값은 `TableRowSpec.cats`(CAT1부터 번호순)에 담기고
`cat1`은 표를 나누는 기준, `subcats`가 표 안 계층이다. 화면·복사·xlsx·PPT는
전부 `TableData.labels()`/`label_values()` 한 쌍으로 열을 만든다 — 라벨 열
개수를 코드에 박지 말 것. 세로 병합 키에는 **상위 CAT을 포함**해서 상위가
바뀌면 하위 병합이 끊기게 한다. 리포트 구성 화면의 드래그 결과는
`export/template_writer.py`가 원본 엑셀에 되쓴다(.bak 백업 후 캐시 무효화).

## 릴리스

`src/etreport/__init__.py`의 `__version__` 하나가 태그(`vX.Y.Z`)·자산 이름·업데이트
체커 비교의 기준이다. 릴리스 시 이 값과 `CHANGELOG.md`를 함께 올린다.
(현재 `__version__`/`pyproject.toml`은 1.3.0인데 CHANGELOG는 1.7.3까지 있다 —
다음 릴리스 전에 맞춰야 한다.)

업데이트는 **자산 확장자로 교체 방식을 정한다**(`update/apply.py: plan()`).
`.exe`면 파일 하나를 `copy /Y`로 덮어쓰고, `.zip`이면 예전처럼 풀어서 robocopy로
폴더에 붓는다(`/MIR`은 쓰지 않는다 — zip에 없는 파일까지 지운다). 어느 쪽이든
**앱이 끝난 뒤** 배치가 교체한다: Windows에서 실행 중인 exe·dll은 잠겨 있다.
`checker.pick_asset()`은 둘 다 올라와 있으면 **exe를 먼저** 고른다.

## 사내 식별자 (공개 전 확인 — `PUSH.md`)

`update/checker.py`의 `API_BASE/OWNER/REPO`, `build/build_release.py`의
`--repo pde-tools/et-report`, `data/querybuilder.py`의 테이블 `eds.f_et_test`,
`docs/`의 item alias·step ID가 사내 정보다. `.gitignore`가 `*.duckdb`,
`*.parquet`, `*.xlsx`, `settings.json` 등을 막아 실제 계측 데이터는 커밋되지 않는다.

## 참고 문서

`docs/ui-mockup-v12.html`은 **배치·동작**의 기준 목업이다 — 색·모서리·글자는
2026-08-15 리디자인 이전(애플풍 라이트) 그대로이니 **시각은 이 목업이 아니라
`ui/theme.py`를 따른다**. `design-plans/instrument-console-redesign.md`가 그
리디자인의 근거와 결정을 남긴 문서다.
`docs/data-report-tool-plan.md`가 계획서다(코드 주석의 "계획서 §N" 참조 대상).
`README.md`의 폴더 구조 표는 일부 모듈(session/aggregate/compat/loader/fonts 등)이
빠져 있어 최신이 아니다.
