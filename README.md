# ET Report Tool

사내 ET 계측 데이터 → 리포메터 가공 → 탐색/요약 → 원클릭 PPT.

## 실행

**개발 (설치 없이)**
```
pip install PySide6 polars pyarrow duckdb matplotlib python-pptx requests packaging
python app.py            # 실사용 모드 (빈 상태로 시작)
python app.py --demo     # 데모 — DB·Excel·bdq 없이 모든 기능 확인
```
`bigdataquery`(추출)·`xlwings`(엑셀 읽기/쓰기)는 사내 PC에서만 필요합니다.

**테스트·린터**
```
pip install pytest ruff
python -m pytest              # 기본 (수 초)
python -m pytest -m slow -s   # 실측 규모: item 1000개 · 1일 20만 행
ruff check .
```
Excel도 bdq도 없이 리포메터·템플릿·적재·분석 로딩을 전부 검증합니다.
사내 PC에서 앱으로 직접 확인할 testset은 아래 명령으로 만듭니다 —
리포메터 xlsx, 추출 결과와 같은 모양의 long parquet, 그리고 **정답표**가
함께 나옵니다.
```
python tools/make_testset.py --out C:\temp\ettest --days 7 --load
```

**한글 폰트 (Windows/macOS/Linux 공통)**

UI와 그래프 폰트는 `src/etreport/fonts.py` 가 실행할 때 OS에 맞게 고릅니다 —
Windows는 맑은 고딕, macOS는 Apple SD Gothic Neo, Linux는 설치된 Noto CJK /
나눔고딕을 잡습니다. WSL에서는 윈도우 폰트(`/mnt/c/Windows/Fonts`)를 그대로
씁니다. 한글 폰트가 하나도 없는 리눅스라면 실행 로그에 경고가 뜨는데, 셋 중
아무거나 하면 됩니다.

```
sudo apt install -y fonts-noto-cjk fonts-nanum     # ① 시스템에 설치
cp MyFont.ttf src/etreport/assets/fonts/           # ② 앱에 동봉(빌드에도 포함)
ETREPORT_FONT="NanumGothic" python app.py          # ③ 패밀리·파일경로 직접 지정
```

## 추출 진행 상황

[데이터] 화면 하단에 단계·진행률·로그가 항상 떠 있습니다. 어느 단계에서
시간이 가는지 초 단위로 남으므로 병목을 바로 알 수 있습니다.

```
리포메터 열기: reformatter_M2.xlsx [M2]
  REAL 48 · ADDP 12  (2.4초)
  ⚠ 31행 Vt spread: 참조 불가 P040_X — 이 item을 제외했습니다
추출 완료 — 1,284,003행 (long) · 46.2초
리포메팅 시작 — 파일 7개 · ADDP 12개
  파일 1/7  183,429행 → 219,115행  (1.1초)
적재 완료 — 254,110행 · 11.8초
── 전체 68.1초 ──
```

ADDP는 수식을 polars 식으로 번역해 벡터 계산합니다. 번역할 수 없는 수식만
행 단위로 떨어지며, 그 사실이 로그에 남습니다.

## Excel 접근 — xlwings 전용 + 캐시

사내 보안상 Excel 읽기/쓰기는 **xlwings만** 가능합니다. COM으로 Excel을 띄우는
비용이 커서 `data/xlio.py`에 캐시 계층을 뒀습니다.

- 한 파일에서 여러 시트를 읽을 때 Excel을 **한 번만** 띄웁니다
- 읽은 결과는 `%APPDATA%\ETReport\xlcache\*.parquet` 에 저장 → 두 번째부터는
  Excel을 아예 열지 않습니다
- 원본의 수정시각·크기가 바뀌면 자동 무효화됩니다
- 상단바 [도구] → [Excel 캐시 비우기]로 비울 수 있고, 리포메터 창의 [새로 읽기]는
  캐시를 무시합니다

## DuckDB 구조

기준 테이블은 **`et_data`** — 손코딩하던 시절과 같은 이름이라
`select * from et_data` 가 그대로 동작합니다. 이 툴이 새로 만드는 DB도
같은 이름으로 만듭니다.

기존 DB는 **읽기 전용**으로 열고 컬럼 이름은 자동 인식합니다
(`data/compat.py`의 별칭 사전: `root_lot_id|lot_id|lot`, `wafer_id|wafer|slot_no`,
`chip_x_pos|die_x`, `tkout_time|create_dttm|meas_time` …).
`item_id`/`value` 형태의 long 테이블이면 읽으면서 PIVOT으로 wide화합니다.
`et_data`도 `fact`도 없으면 테이블 목록에서 고르게 합니다.

제외 포인트는 **원본 DB에 쓰지 않습니다** —
`%APPDATA%\ETReport\exclusions\<DB이름>_<해시>.json` 에 DB 경로별로 저장되어,
DB를 조회 전용으로 유지하면서도 세션 간 유지됩니다.

## 분석 화면의 동작 원칙 — 필요할 때만 읽고, 필요할 때만 계산

파일을 고르는 동안에는 **아무것도 읽지 않습니다.** 경로만 담아 두었다가
[적용]을 누를 때 한 번에 읽고 검증합니다.

```
[적용] ─ 리포메터(Excel 1회) → 템플릿(1회·같은 파일이면 배치) →
         실험 조건 → DuckDB(읽기 전용)
```
검증 결과·제외된 항목은 한 번에 모아 보여줍니다. 이 구성은 레일 맨 위
프리셋 줄에서 이름을 붙여 저장·전환·삭제할 수 있습니다(`settings.json`).

표와 plot도 자동으로 다시 계산하지 않습니다.
- 요약 — **[표 만들기]**
- 탐색 — **[그리기]**
- 리포트 구성 — **[미리보기]**

바뀐 게 있으면 버튼이 주황색이 되고, 해당 화면을 보고 있을 때만 자동
갱신됩니다. 집계는 `model/aggregate.py`의 group_by 한 번으로 끝냅니다
(item×wafer마다 훑던 방식 대비 수십 배 빠름).

**실사용 흐름**
1. [데이터] — DuckDB 경로 지정(**없는 파일명을 적으면 새로 생성**),
   리포메터 지정, 조건 입력 → 추출하고 적재
2. 적재가 끝나면 그 DB가 [분석]에 자동 연결됩니다
3. [분석] 왼쪽 레일 — DB·Plot/Table 템플릿·리포메터·실험 조건 파일을 고른 뒤
   **[적용]**(F5) → REPORT 선택 → 필요하면 프리셋 `⋯`로 이름 붙여 저장.
   무엇을 채워야 할지 모르겠으면 상단바 `?`(F2) — 다음 한 곳만 짚어 줍니다
4. 탐색·리포트 구성에서 점 클릭 제외(→ 사이드카에 영속),
   [PPT 생성] → 실제 pptx 저장
5. 리포트 구성에서 슬롯을 **드래그**해 배치를 바꾸고 [템플릿에 저장] →
   plot 템플릿 엑셀의 order/x/y/제목에 반영(.bak 백업 생성)
6. 상단바 [도구] → [SQL 조회 · 내보내기] → 원하는 조건만 SQL로 뽑아
   CSV/parquet/SBDF 저장

**배포 (exe)**
```
python build/build_release.py            # dist/ETReport.exe (단일 파일)
python build/build_release.py --onedir   # 예전 폴더 배포 + zip
python build/build_release.py --publish  # 사내 GitHub 릴리스까지
```

## 폴더 구조 — 무엇이 어디에
```
app.py                    ★ 개발 실행 진입점  (python app.py)
src/etreport/
├─ app.py                 부팅: CLI·로깅 → 폰트·QSS·설정·카탈로그 → 상태 → 창
├─ schedule.py            예약 실행(--run-extract) — 창 없이 추출·적재, 종료 코드로 보고
├─ resources.py           번들 파일 찾기(exe의 sys._MEIPASS 포함) — 경로는 여기로만
├─ buildinfo.py           빌드 스탬프(시각·git 해시) — "고쳤는데 exe가 그대로"를 가른다
├─ demo.py                데모 모드 진입 — 상태 채우기 + 번들·가짜 소스 준비
├─ demo_data.py           데모 데이터 정의(리포메터·템플릿·실험 조건·raw 측정값)
├─ demo_bundle.py         데모 번들 파일 생성(DuckDB·csv·xlsx·안내 문서)
├─ demo_sources.py        데모용 가짜 사내 소스(추출·계측·tracking·S3)
├─ fonts.py               한글 폰트 해결(Qt·matplotlib 공용, WSL/리눅스 포함)
├─ paths.py               %APPDATA%\ETReport 하위 저장 위치
├─ config/
│  ├─ settings.py         프리셋 2종(추출/분석) JSON 영속화
│  └─ catalog.py          bdq.columninfo 캐시 (조건 빌더의 타입 정보)
├─ update/                ★ 자동 업데이트
│  ├─ checker.py          GHE 릴리스 조회·버전 비교·자산 내려받기(exe 우선)
│  ├─ dialog.py           [지금 업데이트/나중에/건너뛰기] 창 (+다운로드 스레드)
│  └─ apply.py            종료 후 교체 배치 — exe면 copy /Y, zip이면 robocopy
├─ data/
│  ├─ pipeline.py         추출→리포메팅→적재 한 줄기(Qt를 모른다 — 화면·예약이 공유)
│  ├─ querybuilder.py     타입 인식 조건 → Impala SQL (NOT IN NULL 가드)
│  ├─ extractor.py        청크 플래너 → 메모리 예산 병렬 → long parquet(고정 스키마)
│  ├─ reformatter.py      실컬럼 스키마 · ADDP ast 화이트리스트 · Std(5키 그룹 n-1)
│  ├─ xlio.py             xlwings 단일 창구 + parquet 캐시, csv·tsv 정식 입력
│  ├─ db.py               DuckDB 적재: et_data·load_log·key_hash ANTI JOIN·버킷 피벗
│  ├─ loader.py           읽기 전용 열기(open_readonly)·wide 로딩·절대값 재적용
│  ├─ compat.py           역할 별칭·step_seq 병합·retest QUALIFY·타입 관대한 읽기
│  ├─ exclusions.py       제외/필터 사이드카(DB 경로별 JSON)
│  ├─ exporting.py        적재 후 CSV/SBDF 내보내기 · COPY TO 스트리밍(Qt 없음)
│  ├─ lotcontext.py       조회 조건 기본값을 분석 DB에서 채운다(line·process·기간)
│  ├─ metrology.py        inline 계측(f_fab_wf_met) · subitem 규칙 · top_factors
│  ├─ fabtracking.py      fab tracking(PHOTO=recipe/그 외=ppid) · 이름 있는 컬럼
│  └─ s3.py               S3 탐색·다운로드(boto3 선택 의존성, 자격은 별도 파일)
├─ model/
│  ├─ state.py            AppState + StateBus — 화면 간 단일 진실(hidden·캐시)
│  ├─ session.py          [적용] 한 번에 — 파일 읽기·검증을 LoadReport 하나로
│  ├─ specs.py            PlotSpec/PageSpec/TableRowSpec · PLOT_TYPES · fmt_value
│  ├─ templates.py        plot/table 템플릿 로더+검증 (Report 선택자 · CAT 자유)
│  ├─ aggregate.py        wafer 집계(평균·n-1 표준편차) — 화면·xlsx·PPT 공용
│  ├─ categories.py       boxplot x축 후보·값(lot+wafer 가상 컬럼 포함)
│  ├─ outliers.py         Tukey 이상치 — (step, temp)별 Q1−k·IQR / Q3+k·IQR
│  ├─ coverage.py         멀티 lot 결손 판정(기준 lot·조건 불일치·포인트 중앙값)
│  ├─ wafers.py           lot·wafer 표기 비교(W01·W1·01·1을 한 키로)
│  └─ split.py            실험 매트릭스 · factor 그룹핑 · 혼입(confound) 감지
├─ render/
│  ├─ ranges.py           축 규칙 단일 진실: SPEC∪데이터 ×1.2 · 로그 패턴 · 오버라이드
│  ├─ mpl_renderer.py     그리기 단일 진실 — 화면 캔버스도 이걸 쓴다(화면=PPT)
│  └─ pptgen.py           덱 조립 — 16:9 고정 · plot→표→제외 이력 · 표 split
├─ export/
│  ├─ deckbuild.py        상태 → 덱 입력(슬라이드별 데이터 묶음)
│  ├─ excel.py            요약 집계 · TSV 복사 · xlwings xlsx(병합·서식·틀고정)
│  ├─ template_writer.py  드래그한 배치를 원본 엑셀에 되쓰기(.bak 백업)
│  ├─ templates_sample.py 예시 리포메터·템플릿 파일 만들기
│  └─ manual.py           사용 설명서 PDF(QPdfWriter)
└─ ui/
   ├─ mainwindow.py       [데이터|분석] 워크스페이스 전환 · 상단바 · 도구 메뉴
   ├─ data_ws.py          추출 화면 (+백그라운드 추출 스레드)
   ├─ analysis_ws.py      레일 + 탭 + 인스펙터 + 액션바 조립
   ├─ source_rail.py      왼쪽 레일 — 무엇을 보고 있나(DB·템플릿·lot·추가 소스)
   ├─ inspector.py        오른쪽 인스펙터 — 그것을 어떻게 보일까(탭이 섹션을 넘긴다)
   ├─ actionbar.py        하단 액션바 — 주 동작의 영구 주소
   ├─ guidance.py         무엇이 필수인지 판정하는 한 곳(필요 표시·F2·빈 상태)
   ├─ theme.py            TOKENS — 색·모서리·글자 크기의 단일 진실
   ├─ style.qss           %TOKEN%만 쓰는 스타일시트(hex를 박지 않는다)
   ├─ tabs/               explore · summary · report + common(지연 계산 StaleMixin)
   └─ widgets/
      ├─ autocomplete.py   쉼표 인식 자동완성 (로컬 매칭)
      ├─ cards.py          카드·섹션 공통 위젯
      ├─ plot_canvas.py    화면 캔버스 (PPT와 같은 렌더러) + 클릭 제외
      ├─ group_section.py  그룹 한 곳 — 보이기·색/심볼/크기·REF·편집
      ├─ page_strip.py     리포트 페이지 가로 스트립(높이 36px 고정)
      ├─ worker.py         오래 걸리는 일 백그라운드(단 matplotlib 렌더는 제외)
      ├─ toast.py          확인만 받는 알림(모달은 실패·되돌릴 수 없는 확인에만)
      ├─ sql_dialog.py     자유 SQL — 미리보기 200행 · COPY TO로 파일 직행
      ├─ split_dialog.py   factor 선택 · 혼입 경고 · 그룹 미리보기
      └─ group_dialog.py   단일 lot 조회·화살표 / 멀티 붙여넣기 / 스타일 일괄
build/ETReport.spec       빌드 정의 하나 (build_release.py가 이걸 부른다)
build/build_release.py    PyInstaller onefile → dist/*.exe → gh release
```

## 릴리스 절차
1. `src/etreport/__init__.py` 의 `__version__` 올리기 + `CHANGELOG.md` 작성
2. `python build/build_release.py --publish`
3. 사용자 앱은 시작 시 최신 릴리스와 비교해 업데이트 창을 띄운다 (선택 사항, 강제 아님)

## UI 상세의 기준
화면 배치·동작의 단일 기준은 `ui-mockup-v12.html` (목업). `TODO(ui)` 주석이
모듈과 목업 섹션을 잇는다.
