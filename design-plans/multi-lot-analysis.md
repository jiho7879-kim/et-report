# 멀티 lot 분석 — 개선 계획

작성 시점: 2026-08-15. 요청은 "멀티lot 분석 기능 개선 plan을 수립해 줘. 단일 lot을
참고해 줘"였다.

**구현 완료: 2026-08-16.** §3~§6을 전부 구현했고 `tests/test_multi_lot.py` 44개가
아래 결정을 고정한다. 규칙 요약은 `CLAUDE.md`의 "멀티 lot (§9.2)" 절에 옮겨 적었다.
남은 것은 §8뿐이다.

읽는 순서: **§1 지금 상태**(왜 고쳐야 하는가) → **§2 확정 결정**(무엇을 할지) →
**§3~§6 단계별 계획**(어디를 어떻게) → **§7 불변식** → **§8 범위 밖**.

---

## 1. 지금 어디까지 되어 있나

코드를 훑어 확인한 사실만 적는다. 추측은 없다.

**1-1. 분석 로딩은 lot을 고를 수 없다.** `loader.load_state()`(loader.py:191)는
`compat.select_sql(prof)` 하나를 그대로 실행한다(loader.py:206). WHERE가 없으므로
**DB에 들어 있는 모든 lot이 무조건 분석 대상**이다. 계획서 §9.2의 "lot 선택 →
캐스케이딩 조회"는 그룹 편집 다이얼로그에만 부분 구현돼 있고 로딩 경로에는 없다.
요약 문자열이 `lot 3`이라고 알려 줄 뿐(loader.py:231), 그중 하나만 보고 싶다는
의사를 표현할 자리가 없다.

**1-2. 멀티 lot 탭은 붙여넣기 상자 하나가 전부다.** `GroupDialog._multi_tab()`
(group_dialog.py:570-583)은 안내 라벨 + `QPlainTextEdit` + [적용] 버튼이다. 같은
다이얼로그의 단일 lot 탭이 갖춘 것들 — DB 선택, 4단 연쇄 필터, 미배정/배정 두
리스트, 화살표 배정, 자동 그룹핑 — 이 **하나도 없다**. 매칭 실패는 사후
`QMessageBox` 텍스트로만 알려 준다(group_dialog.py:639-645).

**1-3. 붙여넣기 배정은 항상 전체 범위다.** `_apply_paste()`가 쓰는 키는
`(lot_actual, w, None, None, None)`(group_dialog.py:635)이다. 단일 lot 탭은 필터로
좁힌 step·temp·site를 키에 담는데(§9.1), 붙여넣기는 조건을 따지지 않는다. 같은
wafer를 25 ℃와 125 ℃에서 다른 그룹으로 보고 싶어도 방법이 없다.

**1-4. 자동 그룹핑은 두 가지뿐이다.** `wafer별 / lot별`(group_dialog.py:178).
fab tracking이 뽑아 준 split 조건으로 묶는 길은 도크 [factor 편집]에만 있고, 그쪽은
`gid`를 프레임에 직접 쓰기 때문에(loader.py:221-225) 손으로 고치면 [적용] 때
되돌아간다. 규칙이 두 곳으로 갈려 있다.

**1-5. plot은 lot을 구분하지 않는다.** `mpl_renderer.render()`는
`data: dict[gid → DataFrame]`와 `styles`만 받고(mpl_renderer.py:56-64), 그룹 하나당
색·심볼 한 벌을 쓴다(:101-115). `LOT_A W01`과 `LOT_B W01`이 같은 그룹이면 화면에서
구별할 수 없다.

**1-6. PPT 표는 lot 경계를 모른다.** `split_table()`(pptgen.py:349)은
`[(lot, wafer), …]`를 평평하게 편 뒤 **위치로** 12개씩 자른다. lot 3개면 한 장에 두
lot이 걸치고, lot 헤더가 슬라이드 경계에서 잘린다.

**1-7. 계획서 §9.2에 적혀 있으나 없는 것** — 조회 결과 요약, 다중 lot 커버리지 경고,
fixed_dims 후보를 선택 lot의 교집합으로 제한.

**1-8. 반대로, 이미 lot에 대해 옳게 동작하는 것**(건드리면 안 된다):
`model/wafers.py`가 `W01·W1·01·1`을 한 키로 보고, `split.assignment()`·
`loader.apply_manual_groups()`가 그 키로 비교한다. `state.wafer_columns()`(state.py:77)와
`excel.group_wafer_columns()`(excel.py:71)는 이미 lot별로 묶어 헤더를 만든다.
`metrology_dialog.lots()`(metrology_dialog.py:87)는 `state.data`에서 lot을 읽으므로
**로딩을 좁히면 계측 조회는 자동으로 따라온다**.

---

## 2. 확정 결정

질문 10라운드로 확정했다. 구현은 이 표를 기준으로 한다.

| # | 항목 | 결정 |
|---|---|---|
| D1 | 범위 | 로딩·조회 / 그룹 편집 / 분석 전반(표·plot) / 사내 소스 3종 — 넷 다 |
| D2 | 실무 규모 | 한 번에 **2~3 lot** (표 헤더·PPT 분할은 이 규모에 맞춘다) |
| D3 | lot 선택 | 도크에 **다중선택** 추가. 지금은 전수 로딩 |
| D4 | 좁히는 지점 | **SQL WHERE** (`select_sql`에 `lot IN (…)`) |
| D5 | 선택 UI | 도크의 **접이식 체크 리스트**, 각 줄은 `lot ID + wafer 수` |
| D6 | 선택 저장 | **DB 경로별** 별도 기록(설정 프리셋 아님). 처음엔 전부 체크 |
| D7 | 반영 시점 | **[적용] 다시 눌러야** 반영 — 지연 계산 규약대로 앰버 + `•` |
| D8 | 선택 전파 | 그룹 편집 · inline 계측 · fab tracking에 전파. **SQL 조회 창은 제외** |
| D9 | 필터 후보 | **합집합 + 부분 표시** — 일부 lot에만 있는 값은 `(2/3 lot)` |
| D10 | 커버리지 알림 | **전용 표(다이얼로그)** + **[적용] 결과 로그 줄** 둘 다 |
| D11 | 커버리지 위치·기준 | 도크 버튼 → 다이얼로그. 기준 lot은 **item이 가장 많은 lot**(자동) |
| D12 | 커버리지 범위 | **선택한 lot만** (화면·표와 보는 범위를 맞춘다) |
| D13 | 결손 판정 | ① 값이 전부 NULL ② 측정 조건(step·temp·site) 조합 불일치 ③ wafer당 포인트 수 편차 |
| D14 | 판정 기준 | **기준 lot 대비 비율** — 포인트 수 중앙값이 ±20%를 벗어나면 표시 |
| D15 | plot lot 구분 | **사용자 토글**. 도크 [plot] 절에 두고 설정 프리셋에 저장 |
| D16 | 구분 방식 | **lot이 심볼을 정한다**(그룹 symbol은 토글이 켜진 동안 무시), 범례는 `그룹 (LOT_A)` |
| D17 | PPT 표 분할 | **split 모드일 때만** lot 경계 우선, 한 lot이 12장을 넘으면 그 안에서 재분할 |
| D18 | 멀티 lot 탭 배치 | 위쪽 조회 UI(단일 lot 탭과 같은 모양) + 아래 **접이식 붙여넣기** |
| D19 | 배정 범위 | **체크박스**로 선택 — "현재 필터 범위에만 배정", 기본 꺼짐(=전체 범위) |
| D20 | 자동 그룹핑 모드 | lot별 · wafer별 · **split factor별** |
| D21 | factor 그룹핑 방식 | 현재 배정을 **manual_groups로 굽는다** — 손으로 고칠 수 있고 [적용] 후에도 산다 |
| D22 | split baseline | **기준 lot을 고르면** 그 lot의 조건이 baseline |
| D23 | baseline 추출 | **step마다 그 lot의 다수 조건** |
| D24 | baseline 모델 | `SplitMatrix.baseline: str` → **step→코드 맵**으로 확장(예전 문자열은 전 step 공통으로 읽어 호환) |
| D25 | 계측 순위 | 합친 순위 + lot 내 순위를 **열로 나란히**, 정렬은 **보수적인 쪽** |
| D26 | 구현 순서 | 로딩·조회 → 그룹 편집 → 표·plot → 사내 소스 |
| D27 | DB 범위 | **한 DB 안에서만**. 여러 DB 합쳐 보기는 다루지 않는다(§8) |

---

## 3. 단계 1 — 로딩·조회 (앞 단계가 뒤의 전제다)

### 3-1. SQL에서 좁힌다

`data/compat.py`
- `select_sql(p, dedup_latest=True, lots=None)` — `lots`가 있으면 lot 컬럼에
  `WHERE "<lot>" IN ('…')`를 건다. long·wide, 병합·비병합 **네 갈래 모두**에 들어가야
  한다(long은 `base` CTE, wide는 `inner`/단순 SELECT). 넣는 자리는 `dedup` QUALIFY보다
  **앞** — retest 판정이 좁힌 범위 안에서 돌아야 한다.
- `wafer_index_sql(p, lots=None)` — 그룹 편집이 쓰는 색인도 같은 방식으로 좁힌다.
- `lot_index_sql(p)` **(신규)** — `SELECT lot, count(DISTINCT wafer) AS wafers
  FROM t GROUP BY 1 ORDER BY 1`. 도크 리스트가 쓴다(D5). item 컬럼을 건드리지 않아 가볍다.
- `_quote_lot(v)` **(신규)** — 작은따옴표를 `''`로 이스케이프. lot ID에 따옴표가 들어갈
  일은 없지만, 사용자 입력이 SQL 문자열로 들어가는 유일한 자리라 반드시 거친다.
- **`lots`가 비었거나 None이면 예전과 글자 하나까지 같은 SQL이어야 한다.** 회귀
  테스트가 이걸 고정한다.

`data/loader.py`
- `load_state(state, db_path, table=None, lots=None)` — `select_sql(prof, lots=lots)`.
  요약 문자열(:232)에 `lot 3/12`처럼 **선택/전체**를 적는다.
- `lot_index(db_path) -> pl.DataFrame` **(신규)** — `(lot, wafers)`. 반드시
  `open_readonly()`를 경유한다(직접 `duckdb.connect(read_only=True)`를 부르는 코드가
  하나라도 생기면 그 순간 설정 충돌로 연결이 안 열린다).
- `wafer_index_from_db(db_path, lots=None)` — 전파(D8)용.

`model/state.py`
- `lots_all: list[str]` — DB에 있는 lot 전부(도크 리스트의 재료).
- `lots_selected: list[str]` — 체크된 lot. **빈 리스트 = 전부**로 해석한다(설정 파일이
  없던 예전 상태와 새 상태가 같은 뜻이 되게).

`config/settings.py`
- `Settings.lot_selections: dict[str, list[str]]` — **DB 절대경로 → lot 목록**(D6).
  `Settings.load()`에 `raw.get("lot_selections", {})` 한 줄을 더한다. 필드가 늘어도
  예전 파일이 깨지지 않는 구조라 `SCHEMA_VERSION`은 올리지 않는다.
- `AnalysisConfig.lot_split_symbols: bool = False` — plot lot 구분 토글(D15).

`model/session.py`
- `apply_config()` 4)번 블록에서 `load_state(state, cfg.db_path, lots=state.lots_selected)`.
- DB를 읽은 **직후** 커버리지 요약 줄을 `rep.warnings`에 붙인다(D10, §5-1).

### 3-2. 도크 UI

`ui/analysis_ws.py`
- `_build_dock()` 파일 5행(:116-125) **바로 아래**에 `CollapsibleSection("lot")`을 넣는다
  — 도구 묶음과 같은 위젯이라 도크가 길어지지 않는다(D5). 접힌 상태의 제목에
  `lot 3/12 선택`을 적어 펼치지 않아도 상태를 알 수 있게 한다.
- 리스트는 `QListWidget` + `Qt.ItemIsUserCheckable`, 각 줄은 `PA123  25장`.
  [전체]·[해제] 두 개짜리 ghost 버튼을 머리에 둔다.
- 체크가 바뀌면 `_mark_unapplied()`를 부른다(D7) — [적용] 버튼이 앰버 + `•`가 되고
  상태 레일에 문구가 뜬다. **즉시 다시 읽지 않는다.**
- `_pick_db()`에서 DB를 고르면 `loader.lot_index()`로 목록을 채우고,
  `settings.lot_selections`에 기록이 있으면 복원, 없으면 전부 체크한다.
  DB가 큰 경우를 대비해 `run_in_background`로 돌리고 그동안 리스트는 비활성.
- `_collect_into(cfg)`가 아니라 **`Settings`에 직접** 저장한다(프리셋과 분리, D6).

### 3-3. 커버리지 (D10~D14)

`model/coverage.py` **(신규 모듈, 순수 함수)**

```
build(data, items) -> CoverageReport
  base_lot      : item 수가 가장 많은 lot (동률이면 lot ID 오름차순)
  rows          : lot별 (wafers, points_median, missing_items, cond_only_here,
                  cond_missing, point_ratio)
  lines()       : [적용] 로그에 넣을 한 줄짜리 요약들
```

판정 규칙(D13·D14):
- **결손 item** — 그 lot 행에서 해당 item이 전부 NULL이면 결손. 기준 lot에 있고 그
  lot에 없는 것만 센다.
- **조건 불일치** — `(step, temp, site)` 조합 집합을 기준 lot과 비교해 빠진 것·더 있는
  것을 나열한다.
- **포인트 수 편차** — wafer당 포인트 수의 **중앙값**을 기준 lot과 비교해 비율이
  `[0.8, 1.2]`를 벗어나면 표시. 평균이 아니라 중앙값을 쓰는 이유는 미측정 wafer 한 장이
  평균을 끌어내리기 때문이다.
- 대상은 **선택한 lot만**(D12) — `state.data`에서 바로 계산하므로 추가 쿼리가 없다.

`ui/widgets/coverage_dialog.py` **(신규)** — `lot × (wafer 수 · item 수 · 결손 item ·
조건 · 포인트 비율)` 표. 결손 목록은 **TSV로 복사** 가능(엑셀에 그대로 붙게).
도크 [적용] 결과 라벨 아래에 `커버리지` ghost 버튼을 두고 연다(D11). 경고가 하나도
없으면 버튼을 흐리게 두되 누를 수는 있게 한다.

### 3-4. 테스트

`tests/test_multi_lot.py` **(신규)**
- `lots=None`이면 `select_sql` 결과가 예전과 **문자 단위로 동일**(회귀 방어).
- long·wide × 병합·비병합 네 갈래 모두에 `IN (…)`이 들어간다.
- `IN`이 `dedup` QUALIFY보다 앞에 온다.
- 따옴표가 든 lot ID가 이스케이프된다.
- `lot_index`가 `(lot, wafers)`를 주고, wafer 표기가 섞여 있어도(`W01`/`1`) 수가 맞다.
- `Settings`에 `lot_selections`를 저장→로드했을 때 살아남고, 필드가 없는 예전 파일도
  그대로 열린다.
- 커버리지: 기준 lot 선정 · 결손 item 검출 · 조건 차이 · ±20% 판정 · lot 하나면 경고 0건.

---

## 4. 단계 2 — 그룹 편집

### 4-1. 멀티 lot 탭 다시 짜기 (D18)

`ui/widgets/group_dialog.py`
- `_multi_tab()`을 **위/아래 두 덩이**로 나눈다.
  - 위: lot **다중선택**(체크 리스트) + step·site·temp 3단 필터 + 미배정/배정 두
    리스트 + 화살표 4개 + 자동 그룹핑 콤보. 단일 lot 탭의 위젯 구성을 그대로 쓰되,
    리스트 항목은 `PA123 · W01`처럼 **lot을 앞에 붙인다**(lot이 여럿이라 wafer ID만으로는
    가리킬 수 없다).
  - 아래: `CollapsibleSection("엑셀에서 붙여넣기")` 안에 지금의 `QPlainTextEdit`과
    [붙여넣은 내용 적용] 버튼을 그대로 넣는다. **기본은 접힘.**
- 단일 lot 탭과 겹치는 로직(`_refresh_filters` `_update_counts` `_refresh_lists`
  `_assign` `_move` `_move_all`)은 **위젯 묶음을 인자로 받는 형태로 뽑아** 두 탭이
  같은 함수를 쓰게 한다. 복사해 두면 한쪽만 고쳐지는 버그가 반드시 생긴다.
- 필터 후보는 **합집합 + 부분 표시**(D9): 선택 lot 전부에 있으면 값만,
  일부에만 있으면 `25 (2/3 lot)`. 표시용 문자열과 실제 값을 분리해 `itemData`에 값을
  담는다 — 라벨을 되파싱하지 않는다.
- **배정 범위 체크박스**(D19) — "현재 필터 범위에만 배정", 기본 꺼짐. 켜면
  `manual_groups` 키에 `(lot, wafer, step, temp, site)`를, 끄면 지금처럼
  `(lot, wafer, None, None, None)`을 쓴다. 붙여넣기 경로는 **항상 전체 범위**(지금 동작
  유지) — 두 입력의 결과가 달라질 수 있다는 점을 툴팁에 적는다.

### 4-2. 자동 그룹핑에 `split factor별` (D20·D21)

- 콤보를 `wafer별 / lot별 / split factor별` 셋으로 늘린다. `state.split`이 없으면 그
  항목만 비활성화하고 툴팁으로 "실험 조건을 먼저 불러오세요"를 띄운다.
- `auto_group(mode="factor")`는 `state.split.assignment(state.factors)`가 만든 배정을
  **조회 범위 안에서** `state.manual_groups`에 굽는다(D21). 이러면
  - 손으로 몇 장만 옮겨도 그대로 남고,
  - `loader.apply_manual_groups()`가 실험 조건 배정보다 **뒤에** 걸리므로(loader.py:228)
    [적용] 뒤에도 사용자가 고른 쪽이 이긴다.
- 그룹 스타일은 `split.styles_for(factors)`가 만든 것을 그대로 쓴다 — 색·심볼 규칙을
  다시 정의하지 않는다.

### 4-3. lot 선택 전파 (D8)

- `GroupDialog`가 `state.lots_selected`를 보고 목록을 좁힌다. **선택이 비었으면 전체**
  (다이얼로그는 [적용] 전에도 열리므로 좁힐 근거가 없을 수 있다).
- `wafer_index_from_db(db_path, lots=...)`로 색인 조회 자체를 좁힌다.

### 4-4. 테스트

`tests/test_multi_lot.py`에 추가 / `tests/test_group_dialog.py` 확장
- 멀티 lot 탭에서 lot 2개를 고르면 미배정 리스트에 `lot · wafer`가 lot 순서로 뜬다.
- 필터 후보가 합집합이고, 일부 lot에만 있는 값에 `(2/3 lot)`가 붙는다.
- 배정 범위 체크 on → `manual_groups` 키에 step·temp·site가 담긴다. off → 전부 None.
- `split factor별` 자동 그룹핑 → `manual_groups`에 구워지고, `apply_manual_groups()`를
  다시 걸어도 배정이 같다(멱등).
- 자동 그룹핑 후 한 장을 손으로 옮기면 그 한 장만 바뀐다.
- `state.split`이 없으면 `factor` 모드가 0을 반환하고 예외를 던지지 않는다.

---

## 5. 단계 3 — 표와 plot

### 5-1. [적용] 로그 (D10)

`model/session.py:134` DB 블록 뒤에 커버리지 요약을 `rep.warnings`로 붙인다.

```
[커버리지] PB201은 PA123 대비 item 44개 없음
[커버리지] PB201 wafer 12장 (기준 25장)
[커버리지] PC330 wafer당 포인트 중앙값이 기준의 0.6배
```

`LoadReport.text()`가 이미 `제외 N건:`으로 묶어 12줄까지 보여 주므로 표시 코드는 손댈
필요가 없다.

### 5-2. plot의 lot 구분 (D15·D16)

**렌더러 안에서만 처리한다.** 화면(`plot_canvas.render_args`, plot_canvas.py:52)과
PPT(`deckbuild._plot_data`, deckbuild.py:40)가 각각 `{gid: DataFrame}`을 만드는데, 두
곳에서 lot을 쪼개면 "화면=PPT"가 깨진다. 넘어오는 프레임에는 이미 `lot` 컬럼이 있으므로
렌더러가 안에서 나누면 된다.

`render/mpl_renderer.py`
- `render(..., lot_split: bool = False)` 인자 추가. `_render_trend`에도 그대로 넘긴다.
- `lot_markers(data) -> dict[lot, marker]` **(신규)** — `data` **전체**에 등장하는 lot을
  정렬해 `list(MARKER.values())`(mpl_renderer.py:30, `o s ^ D +`) 순서로 배정한다.
  그룹별로 따로 매기면 같은 lot이 plot마다 다른 모양이 되므로, 반드시 한 번에 정한다.
  lot이 5개를 넘으면 순환시킨다(실무 규모는 2~3, D2).
- 점을 찍는 세 자리 — `render()`의 site 분기(:108-115), `_scatter_aggregate()`(:192),
  `_render_trend()`(:296-299) — 에서 `lot_split`이면 프레임을 lot으로 나눠 lot 심볼로
  찍고, 범례 라벨을 `f"{st.name} ({lot})"`으로 단다. 꺼져 있으면 지금 코드 그대로.
- 그룹의 `symbol`은 토글이 켜진 동안 **무시된다**(D16). 이 사실을 도크 체크박스
  툴팁에 적는다.

`ui/analysis_ws.py` — [plot] 절(:174-178), 로그 축 패턴 아래에 체크박스.
`AnalysisConfig.lot_split_symbols`에 저장하고 `state`에도 실어 두 호출부가 함께 읽는다.
`_mark_unapplied`는 부르지 않고 **[그리기]/[미리보기]를 dirty로** 만든다 — DB를 다시
읽을 이유가 없는 표시 옵션이다.

### 5-3. PPT 표 분할 (D17)

`render/pptgen.py: split_table()` — 평평하게 편 뒤 위치로 자르던 것을
**lot 경계로 먼저 자르고, 한 lot이 12장을 넘으면 그 안에서 다시 자른다.**

- lot 하나짜리 25장: 지금과 같은 `12/12/1` — **기존 동작 불변.**
- lot 2개 × 5장: 지금은 한 장, 바뀌면 **두 장.** 의도한 변화다(lot마다 표 한 장).
- 제목의 `(k/n)`은 전체 조각 수 기준으로 그대로 매긴다.
- **overflow 모드는 손대지 않는다**(D17). `build_deck`(pptgen.py:132·136)의
  `mode == "split"` 분기만 바뀐다.

`state.wafer_columns()`(state.py:77)와 `excel.group_wafer_columns()`(excel.py:71)는
그대로 둔다 — 화면과 xlsx는 가로 스크롤이 되므로 나눌 이유가 없고, 나누면 "표는 한 벌"
규칙(§7.2)이 흔들린다.

### 5-4. 테스트

`tests/test_multi_lot.py` / `tests/test_pptgen_deck.py` 확장
- `lot_split=False`면 예전과 같은 marker·범례(회귀).
- `lot_split=True`면 lot마다 marker가 다르고, 같은 lot은 여러 plot에서 **같은** marker.
- 범례 라벨에 lot이 병기되고, 그룹이 하나뿐이어도 lot 수만큼 항목이 생긴다.
- `split_table`: lot 2개 × 5장 → 2조각 / lot 1개 25장 → 3조각(기존과 동일) /
  lot 2개(25 + 3장) → 3조각 · 각 조각의 `header_lots`에 lot이 섞이지 않는다.
- overflow 모드에서는 조각 수가 1이다.
- 커버리지 경고가 `LoadReport.text()`에 나타난다.

---

## 6. 단계 4 — 사내 소스 3종

### 6-1. baseline을 step별 코드 맵으로 (D22·D23·D24)

`model/split.py` — 여기가 이번 단계에서 가장 조심할 곳이다. 지금
`baseline: str` 하나로 세 가지 일을 한다: REF 판정(`styles_for`, :131), 빈칸 채움
(`from_dataframe`, :100), 화면 문구. baseline이 step마다 다를 수 있게 되면 세 곳이
전부 step을 봐야 한다.

- `SplitMatrix.baseline_codes: dict[str, str]` **(신규)** — step → 기준 코드.
- `baseline` 프로퍼티는 남긴다 — 값이 한 종류면 그 코드를, 여럿이면 `"(step별)"`을
  돌려준다. 화면 문구와 예전 호출부가 깨지지 않는다.
- `styles_for()`의 `is_ref` 판정을
  `all(code == self.baseline_codes.get(step) for step, code in zip(factors, codes))`로.
- `from_dataframe(df, baseline)` — `baseline`이 문자열이면 **전 step 공통**으로 펴서
  맵을 만든다(예전 설정·파일 호환, D24). 맵을 그대로 받는 경로도 연다.
- `AnalysisConfig.split_baseline: str`은 그대로 두고
  `split_baseline_lot: str` **(신규)** 를 더한다. 문자열 baseline은 파일·붙여넣기용,
  lot은 fab tracking용이다.

`data/fabtracking.py`
- `to_split_matrix(df, baseline=None, steps=None, baseline_lot=None)` —
  `baseline_lot`이 오면 `_majority_code`를 **그 lot으로 좁혀 step마다** 부른다(D23).
- `_majority_code(cond, step=None, lot=None)` — 대상을 좁힐 수 있게 인자를 연다.
  좁힌 결과가 비면 전체 다수 조건으로 물러난다(기준 lot에 그 step이 없을 수 있다).

`ui/widgets/split_dialog.py`
- `_from_tracking()`(:115)의 `QInputDialog`가 **`state.lots_selected`를 기본값으로**
  채운다(D8). 지금은 매번 손으로 적는다.
- tracking을 읽은 뒤 **기준 lot 콤보**를 띄운다(후보 = 조회된 lot). 고르면
  `to_split_matrix(..., baseline_lot=...)`로 다시 만들고, 라벨에
  `기준(REF) PA123 · step별 코드 3종`처럼 적는다.

### 6-2. 계측 순위 (D25)

`data/metrology.py: top_factors()`
- 반환 스키마에 `r_within`(lot 내 상관을 n 가중 평균), `lots`(계산에 쓴 lot 수)를 더한다.
- lot 내 상관: lot마다 wafer 집계로 피어슨을 구해 `Σ(n_i·r_i)/Σn_i`. wafer가 3장
  미만인 lot은 뺀다(지금 전체 상관의 `len(pairs) < 3` 규칙과 같은 기준).
- 정렬 `score`는 **보수적인 쪽** — `min(|r|, |r_within|)`과 t 항 중 큰 값. lot 평균 차이
  때문에 부풀려진 쌍이 위로 올라오지 않게 하는 것이 이 결정의 목적이다.
- lot이 하나면 `r_within == r`이 되어 지금과 순위가 같다(회귀 안전).

`ui/widgets/metrology_dialog.py` — 결과 표에 `r_within` 열을 더하고, `r`과 부호가
다르거나 크기 차가 2배를 넘으면 그 줄에 `lot 효과 의심` 표식을 붙인다.
`state.met_top`이 그대로 PPT 슬라이드로 나가므로(deckbuild.py:142) 열이 늘면 슬라이드도
따라 늘어난다 — `pptgen`의 factor 슬라이드가 열 수를 하드코딩하지 않는지 확인한다.

### 6-3. 테스트

`tests/test_multi_lot.py` / `tests/test_fabtracking.py` / `tests/test_metrology.py` 확장
- `baseline_codes`가 step마다 다른 코드를 담고 `styles_for`의 REF가 그 조합에만 붙는다.
- 문자열 baseline으로 만든 예전 경로가 전 step 같은 코드로 펴진다(호환).
- `to_split_matrix(baseline_lot="PA123")` → step마다 그 lot의 다수 조건이 기준이 된다.
- 기준 lot에 없는 step은 전체 다수 조건으로 물러난다.
- `top_factors`: lot이 하나면 `r_within == r` · lot마다 평균만 다르고 lot 내 상관이 0인
  가짜 데이터에서 `score`가 낮게 나온다 · `lots` 열이 맞다.
- `split_dialog`의 lot 기본값이 `state.lots_selected`에서 온다.

---

## 7. 지켜야 할 불변식

이번 작업이 단일 진실을 여럿 건드린다. 아래가 깨지면 화면과 PPT가 갈라진다.

| 규칙 | 위치 | 이번에 조심할 점 |
|---|---|---|
| 분석 SELECT | `compat.select_sql` | 네 갈래(long/wide × 병합/비병합) 전부에 WHERE를 넣는다. `lots`가 없으면 예전 SQL과 동일 |
| 읽기 전용 연결 | `loader.open_readonly` | lot 목록 조회도 반드시 경유. 직접 `connect(read_only=True)` 금지 |
| `key_hash_expr()` | `data/db.py` | **건드리지 않는다.** lot 필터는 읽기 쪽 이야기이고 적재와 무관하다 |
| lot·wafer 표기 비교 | `model/wafers.py` | 새 비교를 만들지 말고 `lot_key_expr`/`norm_lot`을 쓴다 |
| 그리기 | `render/mpl_renderer.py` | lot 심볼은 **렌더러 안**에서. 데이터 만드는 두 곳에서 쪼개지 않는다 |
| 덱 조립 | `render/pptgen.build_deck` | 표는 실험과 무관하게 한 벌, 슬라이드는 16:9 고정 |
| 지연 계산 | `tabs/common.StaleMixin` | lot 체크는 [적용] dirty, 표시 옵션은 [그리기] dirty |
| 그룹 배정 우선순위 | `loader.load_state` :221-228 | 실험 조건 → **그 뒤에** manual_groups. 순서를 뒤집지 않는다 |
| 표 셀 병합 | `pptgen._table_slide` | 병합 먼저, 값 나중 (lot 헤더가 `PA1\nPA1`이 된다) |

`tests/test_step_seq_merge.py`(§10.1)·`test_absolute_reapply.py`(§10.2)·
`test_db_buckets.py`가 지키는 것들을 이번 변경이 건드리지 않는지 **먼저** 돌려 확인한다.

---

## 8. 범위 밖 — 다음 과제

- **여러 DB 파일을 합쳐 보기**(D27). lot이 서로 다른 `.duckdb`에 들어 있으면 지금은
  방법이 없다. 하려면 ATTACH + UNION인데, 스키마 불일치(컬럼 이름이 파일마다 다를 수
  있다 — 그래서 `compat.ROLE_ALIASES`가 있다), `key` 충돌(제외 사이드카는 DB 경로별로
  저장된다), 적재 로그 이중화까지 번진다. **같은 DB에 이어 적재하면 되는 문제**이고,
  적재가 이미 `key_hash` ANTI JOIN으로 중복을 막으므로 우선순위는 낮다.
- ~~CLAUDE.md 반영~~ — 구현과 함께 마쳤다. `ET-Report-Tool-사양.md`의 절 번호
  부여는 아직 안 했다(코드 주석은 `§9.2`로 참조하고 있다).
- 데모 데이터는 이미 멀티 lot 함정을 갖고 있었다 — lot 4개(PA123·PA124·PB331·PC777)에
  wafer 수가 25/8/6장으로 갈리고 PB331에는 `BVox`가 없다. 커버리지 표를 눈으로 보기에
  충분하므로 새 lot을 더하지 않았다.
- 사용 설명서(`tools/make_manual.py`) 재생성 — 도크에 [lot] 절과 [커버리지] 버튼,
  [plot]에 lot 구분 체크박스가 늘었으므로 캡처를 다시 떠야 한다. 사내 PC에서 돌린다.
