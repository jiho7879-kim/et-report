# OOM 근본 대책 — 적재 때 step_seq 병합 · 작업 세트(임시 parquet) · 키 프레임

2026-10-13 · 승인: 사용자(retest=최신·seq 중복=평균 / 예전 DB는 폐기하고 재적재 안내)

## 1. 문제

[적용] → DuckDB 조회 → 표·plot에서 OOM. lot 2개 이상은 거의 불가, full map(site 많음)
이나 step_seq가 여럿인 데다 wafer가 많은 lot은 1개도 못 여는 일이 있었다.

합성 DB(die 6만 × item 400, seq 2개, 실제 값 192MB)로 1회 측정한 결과:

| 읽는 방식 | 상한 300MB | 상한 1GB | 상한 없을 때 피크 |
|---|---|---|---|
| 지금 — seq 병합 SQL(window + item마다 `arg_max`) | OOM | 통과 | 1.9GB |
| 평평한 `SELECT`(적재 때 합친 DB) | 통과 | 통과 | 0.74GB |
| 작업 세트에서 키 + 2열 읽기 | — | — | 134MB |
| 작업 세트에서 item 64개 wafer 집계 | — | — | 182MB |

(피크는 프로세스 RSS, import 몫 약 90MB 포함)

원인은 둘이다.
1. **읽을 때 seq 병합** — window가 item 열 전부를 들고 정렬하고, 그 위에서 item마다
   `arg_max` 상태를 만든다. 데이터의 약 10배를 쓰고 상한이 걸리면 디스크로 흘리지 못한다.
2. **파이썬 프레임** — `행 × 필요한 item 전부`를 올린 뒤 제외·그룹 분할·tracking
   join·wafer 집계가 저마다 한 벌씩 복사한다(`Arrow buffer failed to allocate`의 자리).

## 2. 결정

- 같은 step_seq 안의 재측정(retest)은 **가장 늦은 값**, step_seq가 다른데 같은 item은
  **평균**. 적재 단계에서 정한다.
- 예전 형식 DB(키에 step_seq가 있는 DB)는 **폐기**한다. 변환 도구는 만들지 않고, 이어
  적재를 막고 새 파일로 다시 적재하라고 안내한다. 읽을 때의 `arg_max` 병합은 지운다.
- plot을 wafer 단위로 제한하는 안(요청 3)은 필요 없어진다 — 적재 때 합치면 x(seq 1)와
  y(seq 2)가 한 행에 있다.

## 3. 설계

### ① 적재 때 step_seq 병합 (`data/db.py`)
- 저장 키 `KEY7` = `root_lot_id wafer_id chip_x_pos chip_y_pos temperature step_id
  total_site_cnt`. step_seq·tkout_time은 키가 아니다. die 하나 = 한 행.
- `merge_seq(long)`: (key, seq, item)마다 tkout_time이 가장 늦은 값 → (key, item)마다
  평균. 평균을 낸 칸은 item별로 세어 적재 로그에 남긴다.
- `et_keys` = KEY7 + `tkout_time`(그 측정점의 마지막 시각) + `line_id` + `key_hash`.
  `key_hash_expr()`는 KEY7 기준으로 새로 정하고 **이후 바꾸지 않는다**.
- 형식 표시 테이블 `et_format`. 이것이 없는데 `et_data`·`fact`·`et_keys`가 있으면 예전
  형식 → `Store`가 열기를 거부하고 안내한다. 파이프라인은 추출 **전에** 같은 판정을
  해서 오래 추출한 뒤에 실패하지 않게 한다.
- 이어 적재: 같은 측정점이 다시 들어오면 **더 늦게 잰 것**이면 들어온 값으로 갈아
  끼우고(빈 값은 기존 유지), 아니면 빈 칸만 채운다. 겹쳐 도는 예약 추출에서 retest가
  묻히지 않게 하는 규칙이다. `et_keys.tkout_time`도 늦은 쪽으로 올린다.
- `v_latest`는 키가 이미 유일하므로 `et_data`의 별칭이 된다.
- 리포메터의 seq를 넘나드는 ADDP는 입력을 같은 규칙(seq 평균)으로 합친다.
- 예전 형식 이어 적재 경로(`_load_legacy`)는 지운다.

### ② 평평한 읽기 (`data/compat.py`)
- `TableProfile.merged` — `et_format`이 있으면 참. 이 DB는 QUALIFY도 GROUP BY도 없는
  `SELECT`로 읽는다.
- `merges_seq`·`MERGE_ROLES`·`arg_max` 분기는 지운다. 손으로 만든 DB·예전 DB는 예전의
  병합 없는 경로(retest QUALIFY)로 읽히고, seq가 있으면 [적용] 결과에 재적재 안내가 붙는다.

### ③ 작업 세트 + 키 프레임 (`data/workset.py`, `data/loader.py`)
- [적용]은 DuckDB가 고른 lot·item 범위를 `COPY (SELECT …) TO parquet`으로 흘려 쓰게
  한다. 파이썬을 거치지 않는다.
- `state.data`에는 **키 프레임만** 둔다 — `key lot wafer gid step temp site` + 붙인
  tracking·계측 열. 측정 조건·제외·그룹은 이 프레임에서 걸린다.
- 값은 `loader.frame(state, names)` 하나로 읽는다 — 키 프레임 + 그 item 열(작업 세트에서
  그 열만 읽어 key로 붙인다). 메모리에 이미 있으면(데모·테스트·long DB) 그대로 쓴다.
- 작업 세트에 없는 item은 `ensure_items`가 그 열만 따로 떠 둔다(추가 파일).
- 파일 이름은 DB 정체(경로·크기·수정 시각)와 SQL의 해시 — 같은 조합이면 [적용]을 다시
  눌러도 재사용한다. 최근 2벌만 남기고 [적용] 때 지운다(하루 지난 `.tmp`도).
- 손으로 만든 **long** DB는 예전처럼 통째로 메모리에 읽는다(키를 파이썬에서 만든다).

### ④ 값을 쓰는 곳
- plot·PPT 슬롯: 그 plot의 item만 `frame()`으로 읽는다.
- 요약 표·xlsx·PPT 표·REF Δ·계측 유의 인자: `aggregate.wafer_stats_of(state, …)` —
  item을 `CHUNK_BYTES`(64MB) 단위로 나눠 기존 `wafer_stats`로 집계한다(숫자 동일).
- 이상치 필터·커버리지: 같은 묶음 단위.
- `AppState.active()`는 키 프레임 기준 그대로(캐시 계약 유지). 값이 필요한 곳은
  `frame()` 결과에 제외를 건다.

## 4. 테스트
- 적재 병합: seq가 다른 x·y가 한 행 / 다른 키는 안 합침 / retest 최신 / seq 중복 평균 /
  NULL seq / tkout_time = 마지막 / 이어 적재(늦은 값 반영·이른 값은 빈 칸만) /
  예전 형식 거부 / 병합 형식 SQL에 QUALIFY·GROUP BY 없음.
- 작업 세트: 키만 메모리 / `frame()` 값 = DB 값 / 재사용·정리 / 추가 item / 조건 필터.
- 묶음 집계 = 한 번에 집계(`CHUNK_BYTES`를 줄여 강제).
- 기존 스위트 전부(데모 "DB 값 = 화면 값" 포함).

## 5. 범위 밖
- 예전 DB 변환 도구(폐기 결정).
- 열 캐시(LRU) — `frame()`은 매번 parquet에서 읽고 key로 붙인다. 미리보기가 느려지면 그때.
