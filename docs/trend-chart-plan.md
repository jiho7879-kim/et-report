# Trend chart 구현 플랜 (v2)

> 작성: 2026-08-11 · 상태: **구현 진행 중** (검토 답변 5건 + Mode 컬럼 반영 완료)
> v2 변경: ① W/L 헤더명 확정("W","L") ② 라인=대표값·point=한 줄 ③ X축 단위 표기 없음
> ④ W와 L은 항상 별도 구성 ⑤ **ref_band를 모든 plot에서 삭제** ⑥ **Mode 컬럼(site/avg/med/std) 추가**

## 1. 확정된 데이터 사실 (사용자 확인 + 코드 조사)

| 사실 | 근거 |
|---|---|
| W/L은 리포메터 **alias/itemid가 아니라, 리포메터 파일의 별도 컬럼** (item별 고정값) — 헤더명 `"W"`, `"L"` **확정** | 사용자 확인 3회 |
| et_data의 item 컬럼명 = **alias** (item_id 아님) | `test_pipeline_duckdb.py`, `reformatter.apply()` 개명 |
| "x/y = 반드시 리포메터 ALIAS" 제약은 `templates._validate` (L119) **한 곳에만** 존재 | explore 결과 |
| 렌더러 `mpl_renderer.render()`는 alias 검사 없이 `df[컬럼]`만 사용 (L74-86) | explore 결과 |
| 리포메터 표준 스키마 10컬럼은 계약 (`test_reformatter_load.py`가 고정) | CLAUDE.md |
| 추출부(querybuilder `KEY_COLS` · extractor `ARROW_SCHEMA`)는 w/l 미포함 | 직접 확인 |
| `render()`에 들어오는 data는 **die 레벨 wide 프레임** (key/lot/wafer/gid + item alias 컬럼) | `deckbuild._plot_data`, `plot_canvas.draw_spec` |
| 집계 단일 진실 = `aggregate.wafer_stats` ((lot,wafer) group_by 1회, avg|std 지원) | CLAUDE.md |

## 2. 설계 원칙

1. **리포메터 표준 10컬럼 계약은 깨지 않는다** — W/L은 "옵션 컬럼"으로 추가. 파일에 없으면 `None`(구파일·기존 테스트 무회귀). `COLUMNS` 목록 자체는 바꾸지 않는다.
2. **DB·추출부·피벗은 전혀 손대지 않는다** — W/L은 측정 item이 아니므로 et_data에 넣지 않는다. 매핑은 `state.rf`(이미 로드된 alias 메타데이터)에 실려 분석까지 흐른다.
3. **변형은 "데이터에 W/L 컬럼을 만드는 것"이 아니라 "렌더러가 alias 메타데이터의 W/L을 X좌표로 해석"하는 것** — wide 프레임에 컬럼이 생기지 않으므로 아이템 목록·Summary·`fmt_value` 자릿수 규칙에 오염이 없다.
4. **ref_band는 전체 plot 기능에서 삭제** (scatter 포함 — 사용자 확정). `PlotSpec.ref_band` 필드, 렌더 블록, 탐색 체크박스, demo 인자 모두 제거.
5. **Mode 컬럼(site/avg/med/std)** — plot 템플릿에 **옵션** 컬럼으로 추가. 값에 따라 그릴 데이터 레벨이 달라진다(아래 §4-7). 컬럼이 없으면 `site`(현재 동작 = die 레벨 그대로, 무회귀).
6. 오류 처리: 코드베이스 전반의 "버리고 건너뛰고 보고" 패턴 유지 — 기하 없는 item은 그 item만 skip + `warnings` 추가.

## 3. 데이터 흐름 (Trend chart)

```
리포메터 Excel (표준 10컬럼 + 옵션 "W","L")
  └─ reformatter.load()          → Rule.w / Rule.l (float|None)
  └─ session.apply_config 1단계  → state.rf (기존, 변경 없음)
                                     └─ state.rf.by_alias[item].w / .l  ← 기하값 여기서 해석
템플릿 plot 시트: Type="trend", x="W"|"L", y="A,B,C", Mode="site"|"avg"|"med"|"std"(옵션)
  └─ templates._validate         → trend 행은 x를 기하 컬럼명으로 검증 (y만 alias 검사)
  └─ build_report                → PlotSpec(type="trend", mode=...)
  └─ mpl_renderer.render()       → spec.type=="trend" 분기: (item, group) 라인 + point 스트립
```

## 4. 변경 목록 (파일별)

### 4-1. `data/reformatter.py` — 유일한 데이터 계층 변경 (옵션 컬럼 추가)

- `Rule` dataclass에 필드 추가:
  ```python
  w: float | None = None      # 옵션 기하 컬럼 "W" (폭)
  l: float | None = None      # 옵션 기하 컬럼 "L" (길이)
  ```
- `load()`에서 **컬럼 존재 시에만** 읽기 (`raw.columns`에 있으면):
  ```python
  w=_num(row.get("W")) if "W" in raw.columns else None,
  l=_num(row.get("L")) if "L" in raw.columns else None,
  ```
  (헤더명 `"W"`/`"L"` 사용자 확정 — 상수 `GEOM_COLUMNS`와 중복 정의 금지, reformatter에 하드코딩)
- `COLUMNS`(10컬럼)는 **변경하지 않는다** — W/L은 필수 계약에서 제외.

### 4-2. `model/specs.py` — 상수 + PlotSpec 변경

- `GEOM_COLUMNS = ("W", "L")` 상수 추가.
- `PlotSpec`:
  - `ref_band: bool = False` **삭제** (전면 삭제)
  - `mode: str = "site"` 추가 — `site | avg | med | std` (그릴 데이터 레벨)
  - `type` 주석을 `# scatter | table | box(예정) | trend`로 갱신 (type은 자유 문자열, 필드 변경 불필요)

### 4-3. `model/templates.py` — trend 검증 분기 + Mode(옵션) 파싱

- **Mode는 PLOT_COLS에 넣지 않는다** (옵션 — 없으면 `site` 기본값, 구 템플릿 무회귀).
- `_validate()`:
  - `typ == "trend"`면:
    - `x`는 반드시 `GEOM_COLUMNS` 중 하나 (단일 값) — 아니면 skip + warning. `rf.by_alias` 검사에서 제외.
    - `y`만 기존 alias 검사 (L119의 `ys` 부분).
  - `typ`이 trend/scatter가 아닌 값(빈 값 포함) → 기존처럼 `scatter` 취급(현재 동작 유지).
  - `Mode` 값이 4종(site/avg/med/std) 밖이면 warning + 그 행은 `site`로 기본 처리.
- `build_report()`: `PlotSpec(mode=str(r.get("Mode") or "site").strip().lower(), ...)` 전달.
- `pairs()`는 그대로 — `x="W", y="A,B,C"`는 `min==1` 규칙으로 3쌍 생성됨.

### 4-4. `render/mpl_renderer.py` — 핵심 변경 (trend 분기 + Mode + ref_band 삭제)

**ref_band 삭제**: L124-133의 `if spec.ref_band:` 블록과 `REF_FILL` 상수 제거.

**Mode 처리 (scatter, type=="scatter")**:
- `mode == "site"` → 현재 동작 그대로 (die 레벨 산점도).
- `mode in ("avg", "med", "std")` → **wafer 레벨 집계 점**: 각 group df에 대해
  `aggregate.wafer_stats(df, excluded_key_set, [x_item, y_item], agg=mode)` →
  (lot, wafer)별로 1점씩 `(x_wafer, y_wafer)` 산점도. x/y가 여럿이면 쌍마다.
- 축 범위 `dr` 수집도 **집계된 값 기준**으로 (scatter 경로의 dr 루프를 mode별로 분기).
- excluded는 render 인자(pl.DataFrame, key 컬럼)의 key set으로 변환해 사용.

**trend 분기** — `render()` 첫머리에 `if spec.type == "trend": return _render_trend(...)`.
`_render_trend(spec, data, styles, rf, log_patterns, figsize, excluded, compact, fig)`:
1. `geom = spec.x.strip()` (W 또는 L). items = `[y for _, y in spec.pairs()]`.
2. **item별 X좌표**: `x_i = rf.by_alias[item].w if geom=="W" else .l`. `None`(기하 없음)이면
   그 item은 skip + `log.warning`. **X축 단위 표기 없음** (사용자 확정 — 라벨은 그냥 "W"/"L").
3. **point 스트립** (`mode=="site"`면 die 레벨, 그 외는 wafer 집계 레벨):
   - site: `df[item]` 원본 값들 → `x_i` 위치에 옅은 점 스트립 (alpha 0.45, 크기 작게, zorder 낮게).
   - avg/med/std: `wafer_stats(df, excluded_set, [item], agg=mode)`의 wafer 값들 스트립.
4. **라인 (대표값)** — 사용자 확정 "라인=대표값, point는 한 줄":
   - 대표 집계 `line_agg = "med" if mode == "site" else mode`.
   - `aggregate.group_representatives(df, excluded_set, items, agg=line_agg)` →
     item별 대표값 → `(x_i, rep_i)`를 `x_i` 정렬로 연결한 라인.
   - 각 group(`styles`)별: 색 `st.color`, 마커 `MARKER[st.symbol]`, 범례 `st.name`.
     REF 그룹(`st.ref`)은 회색 `#8E8E93`, 마커 `d`.
5. **스펙·타깃**: item별 SPECLOW/SPECHIGH를 **`x_i` 위치의 세로선**(빨강 실선)으로,
   target 있으면 `x_i` 위치에 파란 `X` 마커. (X축이 기하라 박스/밴드는 의미 없음)
6. **축**: X = `min/max x_i` ×1.2 패딩(`ranges.py` 헬퍼 재사용), 선형 고정.
   Y = point∪대표값 min/max ×1.2, 로그는 `log_patterns` fnmatch로 y item 이름 판정.
7. 나머지(스타일·legend·spine·box_aspect·tight_layout)는 scatter 경로와 동일하게.

**기존 scatter 경로는 건드리지 않는다** → `test_review_fixes.py` 렌더 계약 무회귀.

### 4-5. `model/aggregate.py` — med 지원 + 대표값 헬퍼

- `wafer_stats(data, excluded, aliases, agg)`: `agg == "med"`(polars `median()`) 지원 추가.
- `group_representatives(data, excluded, aliases, agg="med") -> dict[str, float | None]`:
  그룹 전체(제외 반영)의 item별 대표값. wafer_stats 패턴 재사용 —
  (lot, wafer) 집계값들의 평균(기존 "wafer 평균 → group 평균" 규칙과 동일 골격).
- `ref_values`도 `agg=="med"` 자연 지원(같은 분기 사용).

### 4-6. `ui/tabs/explore.py` · `demo.py` — ref_band 제거

- `explore.py`: `chk_ref` 체크박스(L88-91) 제거 + `_axes_changed`의
  `self.state.explore.ref_band = ...`(L115) 제거.
- `demo.py`: `ref_band=True` 인자 3곳(L57, L64, L123) 제거.

### 4-7. `export/template_writer.py` — Mode 라운드트립

- `rows_from_report()`에 `"Mode": s.mode` 추가 — UI 저장 시 Mode가 엑셀에 되써진다.
- 원본에 Mode 컬럼이 없으면 `merged_frame`의 빈 채움 로직이 자리를 지킨다(이미 처리됨).

### 4-8. `ui/widgets/plot_canvas.py` — 클릭 제외 가드

- `_collect_points()`는 `spec.mode == "site"`일 때만 raw die 좌표를 수집
  (집계 모드에서 클릭 → 엉뚱한 die 제외 방지).

### 4-9. (후속, 이번 범위 제외) `ui/tabs/report.py` 인스펙터
trend 행의 x는 기하명으로 고정 — 인스펙터의 x 편집 비활성은 후속으로 미룬다.

## 5. 테스트 계획

| 테스트 | 내용 |
|---|---|
| `test_reformatter_load.py` 추가 | W/L 컬럼 있는 시트 → `Rule.w/l` 파싱 / 없는 시트 → `None` (구계약 무회귀) |
| `test_templates.py` 추가 | `Type="trend"` 행: `x="W"` 통과, `x="Vtlin"`(비기하) 거부, `y` 비-alias → 그 행 skip, `Mode` 파싱/기본값, `ref_band` 관련 제거 확인 |
| `test_analysis_core.py` 추가 | `wafer_stats(agg="med")`, `group_representatives` 계산 검증 |
| `test_mpl_renderer` 신규 (trend 전용) | 가짜 rf(alias 3개, w/l 부여) + 가짜 wide df → trend 렌더: 라인 수·색·범례·point 스트립·spec 세로선·X축 라벨("W") 검증 |
| `test_mpl_renderer` 신규 (Mode) | scatter `mode="avg"` → wafer 수만큼 점 / `mode="site"` → die 수만큼 점 / ref_band 필드 부재 확인 |
| `test_review_fixes.py` 계약 유지 | 기존 scatter 경로 `render(PlotSpec(x="A", y="B"), ...)` 그대로 통과 |
| `test_ui_smoke.py` | 창 조립 무회귀 (chk_ref 제거 반영) |
| 실사용 확인 | 사내 PC에서 실제 리포메터(xlsx, W/L 컬럼)로 [미리보기] — 눈 검증 |

## 6. 확정 사항 (검토 답변 반영 — 미결정 없음)

1. **리포메터 헤더명**: `"W"`, `"L"` 확정 ✅
2. **대표값 방식**: 라인 = 대표값(med 기준), point = 한 줄로 쭉 ✅
3. **X축 단위**: 표기하지 않음 (라벨은 "W"/"L") ✅
4. **W/L 구성**: 항상 별도 구성 — x="W" 플롯과 x="L" 플롯은 별도 템플릿 행/슬롯 ✅
5. **ref_band**: trend뿐 아니라 **전체 plot 기능에서 삭제** ✅

## 7. 구현 순서 (검토 답변 반영 완료 — 바로 구현)

1. `aggregate.py` med + 대표값 헬퍼 → 2. `reformatter.py` W/L 옵션 컬럼 + 테스트 →
3. `specs.py` (GEOM_COLUMNS·mode·ref_band 삭제) → 4. `templates.py` trend/Mode 검증 + 테스트 →
5. `template_writer.py` Mode → 6. `explore.py`·`demo.py` ref_band 제거 →
7. `mpl_renderer.py` trend 분기 + Mode 집계 + ref_band 삭제 + 렌더 테스트 →
8. `plot_canvas.py` 클릭 가드 → 9. 스모크/전체 테스트 → 10. 린트 → 11. 커밋·푸시
