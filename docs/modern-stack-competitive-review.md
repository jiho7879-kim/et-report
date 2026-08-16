# 현대 오픈소스 스택 vs ETReport — 경쟁 리뷰

> 작성: 2026-08-15 · 상태: **초안 v1** (웹 조사 완료, 소스 포함)
> 목적: "현대 OSS 스택이 ETReport를 더 잘·더 싸게 대체할 수 있다"는 주장의
> 가장 강한 공격 시나리오와, ETReport의 DuckDB+Excel-계약 설계가 가진
> 방어 가능한 이점을 판정한다. 사내 공개 전 `PUSH.md` 확인 대상 문구 없음.

---

## 1. 평가 대상 (ETReport)

Windows 데스크톱 앱(단일 사용자, PyInstaller exe). 파이프라인:
Impala 추출 → DuckDB 로컬 저장 → **Excel 계약 리포메터**(CATEGORY/ITEMID/
ALIAS/ABSOLUTE/SCALE/ADDP, 행 순서 ADDP, ast 화이트리스트) → matplotlib
trend/scatter/wafer 플롯 → python-pptx 덱(16:9, merge-before-value).

## 2. 조사 범위 (2026년 8월 시점)

| 구분 | 후보 |
|---|---|
| DuckDB 기반 | DuckDB 자체, Evidence, Observable Framework, Quarto |
| 노트북 | JupyterLab, VS Code, Databricks, Posit Connect |
| BI/임베디드 | Metabase, Superset, Redash, Lightdash |
| SPC/웨이퍼 OSS | wafermap(cap1tan), tsmap, WaferScope, stdf.io, pyspc, pycontrolcharts, qcc(R) |
| 상용 셀프서비스 | Sigma, Hex, ThoughtSpot, Mode, Looker, Power BI, Tableau, Cube |

## 3. 결론 요약

**주장은 부분적으로만 성립한다.** OSS 스택이 ETReport의 *저장·렌더링* 계층은
더 싸게 대체할 수 있지만, *도메인 로직*(Excel 계약 리포메팅, split lot 그룹핑,
wafer 집계 의미론)은 대체재가 없다. 그리고 스택을 조립하는 비용이 툴을
유지하는 비용보다 크다. 가장 강한 공격점은 **비용이 아니라 공유/협업**
(ETReport는 단일 사용자 데스크톱; OSS는 정적 사이트/Connect로 공유 가능)과
**유연성**(고정 GUI vs 노트북)이다.

### 방어 가능한 이점 (ETReport 쪽)
1. **Excel 계약 리포메팅 계층은 OSS에 대응물이 없다.** 시트 행 순서 의존
   ADDP, ast 화이트리스트 수식 파싱, polars 벡터 번역은 베스포크 도메인
   로직 — 조립 스택으로는 *만들어야* 하는 부분, 즉 곧 제품이다.
2. **도메인 패키지가 취약.** pyspc는 GPL+휴면(2016), tsmap은 뷰어일 뿐
   파이프라인 아님, qcc는 R. 웨이퍼맵+SPC+split 분석+PPTX 덱을 OSS로
   조립하면 수개월 프로젝트 — "더 잘·더 싸게"가 아니다.
3. **PPTX 덱 픽셀 제어**(16:9, 병합 먼저, overflow/split)는 python-pptx
   파이프라인이고 Quarto/Evidence는 HTML/PDF를 낸다.
4. **에어갭 + 제로 옵스.** PyInstaller exe 하나 — 서버·Node·도커·클라우드
   계정 불필요. 팹 엔지니어의 잠긴 PC에서 더블클릭 실행은 실제 배포 이점.
5. **DuckDB 선택은 검증됨.** 로컬 DuckDB + 읽기전용 연결 + 버킷 불변 적재는
   2026 컨센서스 아키텍처(하이브리드: 웨어하우스가 진실, 에지에서 DuckDB)와
   일치. "DuckDB가 낡았다"는 주장은 사실이 아니고, "commodity"라는 건
   ETReport에 *유리*하다(같은 엔진을 OSS도 씀).

### 공격이 유효한 곳 (OSS 쪽)
- **비용**: 모든 구성요소 무료(MIT/BSD/Apache). ETReport 비용은 내부 개발
  시간뿐.
- **유연성**: 노트북이 임의 분석(ML·클러스터링·RCA)에 유리; Evidence/Quarto
  리포트는 git 리뷰 가능.
- **차트 품질**: Observable Plot/D3/Plotly가 인터랙션에서 matplotlib보다 우위.
- **공유**: 정적 사이트 + Posit Connect가 브라우저 공유 가능 (ETReport는 단일
  사용자).

## 4. 옵션별 요약

### 4-1. DuckDB (기반)
- MIT, v1.5.x. GB→수백 GB·1~5 동시 사용자 스위트 스팟, laptop에서 1억 행
  미만 1초 내. Parquet/CSV/JSON 직접 읽기, Arrow zero-copy.
- 약점: 단일 writer 동시성(한 프로세스만 쓰기), WASM은 브라우저 메모리
  2~4GB·단일 스레드 제약.
- 출처: https://duckdb.org/faq · https://pypi.org/project/duckdb

### 4-2. Evidence
- "BI as code" — SQL-in-Markdown → 정적 사이트. MIT, ~6.8k stars.
- DuckDB를 1급 데이터 소스 지원. Svelte 컴포넌트(`<DataTable>` 등), 템플릿
  페이지(`[customer_id].md`), URL 필터.
- 배포: 정적 사이트(빌드 시점에 쿼리 실행, 데이터가 HTML에 박힘). Cloud는
  RLS·SSO·스케줄.
- 비용: OSS 무료 자가호스팅, Cloud Pro ~$25/user/mo(3rd party).
- **약점(중요)**: OSS에 RLS 없음 — 운영진 답변은 "사용자 그룹별로 정적 사이트
  N개 배포". 웨이퍼맵/도메인 차트 없음(커스텀 Svelte 필요). Node 18+ 툴체인.
  PPTX 출력 없음(HTML뿐).
- 출처: https://github.com/evidence-dev/evidence · https://github.com/evidence-dev/evidence/discussions/3026 · https://docs.evidence.dev/core-concepts/data-sources/duckdb

### 4-3. Observable Framework
- 오픈소스 정적 사이트 생성기, ISC. ~3.6k stars. 데이터 로더는 모든 언어
  (Python/R/SQL), 빌드 시 스냅샷. DuckDB-WASM 내장(SQL 코드블록, Arrow 반환).
- 차트: Observable Plot/D3/Mosaic/Vega-Lite — OSS 중 최고 인터랙티브.
- 약점: 정적 스냅샷(OSS에서 사용자별 실시간 쿼리 불가), 내장 인증 없음,
  WASM 메모리 한계, JS 중심(Python은 로더에서만).
- 출처: https://github.com/observablehq/framework · https://observablehq.com/framework/lib/duckdb

### 4-4. Quarto
- 과학/기술 출판 시스템, MIT(v1.4+). 파라미터화 리포트가 핵심 — 템플릿 하나로
  lot/제품별 리포트 다수 출력. DuckDB는 Python/R 코드 청크로 사용(SQL 블록 없음).
- 배포: Quarto Pub(무료), Posit Connect(상용). PPTX/PDF/Word/HTML 출력.
- 약점: **파라미터화 Quarto는 Posit Connect 미지원**(R Markdown과 달리);
  내장 인증 없음; PPTX는 슬라이드 스타일이지만 python-pptx만큼 픽셀 제어 안 됨.
- 출처: https://quarto.org/license · https://posit.co/blog/parameterized-quarto

### 4-5. JupyterLab / VS Code
- 무료(BSD/MIT). 팹에서 WM-811K 웨이퍼맵 분류·SPC·이상탐지는 노트북 표준
  워크플로.
- 약점: 노트북은 JSON이라 git diff 불량; 실시간 협업은 `jupyter-collaboration`
  확장인데 **권한·감사추적 없음**; 대용량은 DuckDB/polars 없으면 느림; 공유/
  배포 내장 없음.
- 출처: https://hex.tech/blog/jupyter-lab-vs-jupyter-notebook · https://github.com/whdpanda/semiconductor-yield-analytics

### 4-6. Databricks
- 상용 lakehouse. Intel/AMD/NVIDIA/ASML 고객. 3팹 통합·월 120억 레코드·
  medallion 아키텍처 사례 다수. SQL Serverless ~$0.70/DBU(Premium), Standard
  티어 폐지. 중앙값 연 $250k.
- 약점: **클라우드 전용(에어갭 불가)**, 비용 불확실, 단일 엔지니어 laptop
  워크플로에 과함.
- 출처: https://www.databricks.com/blog/semiconductors-data-intelligence-platform · https://flexera.com/blog/finops/databricks-pricing-guide · https://www.databuzzltd.com/case-studies/semiconductor-yield-analytics

### 4-7. Posit Connect / Workbench
- 상용 R+Python 배포 플랫폼 — Shiny/Quarto/Jupyter/Streamlit/Dash/FastAPI 전부
  `rsconnect`로 배포. 스케줄 실행, 인증, RBAC.
- 약점: 사용자당 상용 라이선스(리스트 가격 공개 없음), 온프렘 옵스 부담,
  R 중심 유산.
- 출처: https://posit.co/products/enterprise/connect/ · https://docs.posit.co/connect/user/publishing-cli-apps

### 4-8. Metabase / Superset / Redash / Lightdash
- Metabase: 오픈코어(AGPL). CE 무료 자가호스팅; 샌드박싱·SSO·감사는 유료.
  ~$100/mo(Cloud Starter 5유저), Enterprise ~$20k/yr. 스케일에서 성능 약점,
  복잡 분석은 SQL 의존.
- Superset: Apache 2.0, RBAC/SAML/LDAP 무료. 그러나 **옵스 부담** (웹 팟+
  Celery+Redis+Postgres 메타데이터), 학습곡선 높음, DuckDB는 클라이언트-서버
  툴로 파일 DB를 봄.
- Redash: BSD-2, **Databricks 인수 후 유지보수 모드**, 마지막 OSS 26.3.0
  (2024-03). 새 도입 비추천.
- Lightdash: dbt-네이티브, 세마틱 레이어 YAML, 상용 클라우드 ~$800/mo부터.
- 출처: https://blog.elest.io/apache-superset-vs-metabase-vs-redash-which-open-source-bi-tool-to-self-host-in-2026/ · https://getfairview.com/blog/metabase-vs-superset

### 4-9. 웨이퍼맵/SPC OSS
| 패키지 | 라이선스 | 상태 | 능력 |
|---|---|---|---|
| wafermap (cap1tan) | MIT | 활성, v0.3.2 (2025-12) | 인터랙티브 HTML 웨이퍼맵, 엣지 제외, hover, PNG |
| tsmap (wafertools) | OSS | 활성, v0.1.24 (2026-08) | **데스크톱+브라우저** STDF/ATDF/CSV/JSON, Rust 파서 ~96MB/s, bin pareto, 상관행렬, 100% 로컬(Tauri v2) |
| wafer-map | — | 휴면 (2023-11) | wxPython GUI, SEMI M1-0302 |
| stdf2map | — | 휴면 (2018, 48⭐) | Python STDF→bin map |
| WaferScope | OSS | 활성 | 브라우저만, CSV/Excel/KLARF, 100% 클라이언트 |
| stdf.io | 무료 | 활성 | 브라우저 STDF 뷰어 (wafer맵·CPK·bin pareto·파라미터 trend) |
| pyspc | GPL-3 | **휴면 (2016)** | Shewhart/EWMA/CUSUM/P·NP·C·U/T²·MEWMA |
| pycontrolcharts | MIT | 신규 (2026-03) | pandas+Plotly |
| qcc (R) | GPL≥2 | v2.7 | **사실상 표준** — Shewhart/CUSUM/EWMA/capability/다변량 |

- 출처: https://github.com/wafertools/tsmap · https://pypi.org/project/wafermap/ · https://github.com/carlosqsilva/pyspc · https://pypi.org/project/pycontrolcharts/ · https://cran.r-project.org/package=qcc · https://waferscope.dev/ · https://stdf.io/

### 4-10. 상용 셀프서비스
- Sigma(스프레드시트 네이티브, 플랫 요금), Hex(실시간 협업 노트북, 웨어하우스
  스케일), ThoughtSpot(~$25/user/mo), Mode/Looker/Power BI($10~/user/mo)/
  Tableau($75~/user/mo), Cube(세마틱 레이어).
- **공통점**: 전부 클라우드 웨어하우스(Snowflake/BigQuery/Databricks)를 전제.
  엔지니어 laptop의 로컬 DuckDB 파일을 1차 배포로 지원하는 제품은 없다 —
  정확히 ETReport의 니치.
- 출처: https://www.holistics.io/blog/self-service-analytics/ · https://www.basedash.com/blog/best-self-service-analytics-tools-compared-2026

## 5. 팹에서 실제 배포 패턴 (서버 vs laptop)

1. **에어갭 온프렘(보안 제약 팹)**: "모델은 온프렘 또는 에어갭 환경에서
   실행, 데이터는 파운드리를 떠나지 않는다". 공급사도 "완전 에어갭, 아웃바운드
   없음"으로 배포 — 클라우드 전용(Databricks/Hex/Sigma/Evidence Cloud)을
   죽이고 로컬 퍼스트 도구를 선호 → **ETReport의 배포 모델과 동일**.
2. **클라우드 lakehouse(팹 간 통합)**: 3+ 팹·월 120억 레코드 규모는
   Databricks-on-AWS + medallion. 플랫폼 플레이이지 엔지니어 개인 도구가 아님.
3. **Laptop 로컬(단일 엔지니어)**: DuckDB + Jupyter/VS Code + Evidence/
   Observable/Quarto + tsmap/WaferScope — "현대 OSS 스택" 공격이 조립할 패턴.
- 출처: https://case-studies.ai/use-cases/production-and-quality/PQ-005-semiconductor-yield-optimization/ · https://sandboxsemiconductor.com/platform/deployment · https://github.com/windmill-labs/windmill/issues/8258

## 6. 후속 (다음 리뷰 라운드)

- 팹 엔지니어 실제 워크플로 인터뷰 (server vs laptop 선택 근거).
- 리포메터 Excel 계약의 산업 표준 대체 존재 여부 추가 조사 (e.g. Yield
  Management Systems, JW (Jon) Wolfie 계열) — 단, 사내 식별자 공개 주의.
- PPTX 덱 자동 생성 vs Quarto/HTML 리포트 실사용 비교 (수신자 반응).
