# 사내 데이터 리포트 자동화 툴 — 설계 계획서

> 상태: **초안 v0.1** / 최종 수정: 2026-08-10
> 이 문서의 §14는 **미결 쟁점 목록**입니다. 답이 채워져야 구현 착수가 가능한 항목에 🔴 표시했습니다.

---

## 1. 목적과 범위

사내 API(Impala)에서 ET 계측 데이터를 추출하여, 리포메터 규칙으로 가공하고, 조건별 summary 테이블과 scatter plot을 만들어 **한 번의 조작으로 PPT 덱을 생성**하는 데스크톱 툴.

### 산출물
| 산출물 | 소비자 | 형식 |
|---|---|---|
| PPT 덱 | 리포트 수신자 | .pptx (페이지당 최대 2×3=6 슬롯) |
| wide 데이터 | Spotfire, 엔지니어 | CSV / SBDF |
| 누적 DB | 툴 자신 | DuckDB (wide) |

### 비목표 (v1 제외)
- 실시간 모니터링 / 알림
- 다중 사용자 동시 편집
- 웹 배포

---

## 2. 기술 스택

| 영역 | 선택 | 비고 |
|---|---|---|
| 언어 | Python 3.11+ | |
| GUI | **PySide6** | 확정 |
| 데이터 추출 | `bigdataquery` (사내) + `ThreadPoolExecutor` | I/O bound, 스레드로 충분 |
| 중간 저장 | Parquet (long) + `pyarrow` | 청크별, 스키마 불변 |
| DB/쿼리 | **DuckDB** (MIT, 무료) | wide 누적 테이블 |
| 데이터 변환 | Polars (보조) | DuckDB ↔ Polars zero-copy |
| Excel | **xlwings** (사내 제약) | 리포메터/템플릿 읽기 전용 용도 |
| 화면 plot | **PyQtGraph** | 대용량 산점도, 인터랙션 |
| PPT plot | **matplotlib** | 인쇄 품질, 벡터 |
| PPT 생성 | `python-pptx` | 템플릿 placeholder 채우기 |
| 검증 | `pydantic` | 스펙/시트 스키마 |
| 패키징 | PyInstaller `--onedir` | Nuitka는 대안 |
| 의존성 | `uv` | |
| 로깅 | `loguru` 또는 stdlib | |

### 왜 유료 DB가 필요 없는가
DuckDB는 MIT 라이선스, 임베디드, 컬럼 지향, out-of-core 처리 지원. 별도 서버 프로세스가 없어 exe 배포와 궁합이 좋음.

---

## 3. 전체 파이프라인

```
[1] 추출      Impala ──청크 병렬──> long parquet (11컬럼)
                                        │
[2] 리포메팅   scale/alias/파생item ─────┤  (long 상태에서 수행)
                                        │
[3] 재파티션   키 해시 → 버킷 512개 ─────┤  (1회 스트리밍 재작성)
                                        │
[4] 적재      버킷별 피벗 ──────────────> DuckDB fact (wide, 누적)
                                        │
[5] 세션      lot 선택 + 구분자 고정 ────┤
              그룹/ref/exclusion         │
                                        ├──> v_session (뷰)
[6] 산출      ├─ Summary 테이블 ─────────┤
              ├─ Plot (화면/PPT) ────────┤
              └─ wide CSV/SBDF ──────────┘
                                        │
[7] 조립      SlotSpec → python-pptx ───> .pptx
```

### 설계 원칙 3가지
1. **스펙 하나 → 렌더러 여럿.** 화면과 PPT는 같은 스펙을 다르게 그린 결과일 뿐. 화면은 상태가 아니다.
2. **공유 상태는 세션 소유.** exclusion·그룹·ref는 plot이 아니라 세션에 속한다. 뷰 하나(`v_session`)로 모든 소비자에 전달.
3. **메모리는 폭으로 통제.** 행(날짜)이 아니라 열(item 배치)·버킷 수로 피크 메모리를 직접 제어한다.

---

## 4. 데이터 추출

### 4.1 API
```python
import bigdataquery as bdq
df = bdq.getData(query)          # query: SQL 문자열
```
- 대상 테이블: `eds.f_et_test`
- 필수 조회 컬럼: `line_id`, `tkout_time` (유저가 지워도 강제 주입)
- 백엔드 엔진: **Impala**

### 4.2 Low-code 필터 UI

```python
@dataclass
class Condition:
    column: str
    raw_input: str                          # 유저 원문 보존
    mode: Literal["auto", "regexp"] = "auto"
    exact: bool = False                     # regexp일 때 ^...$ 자동 감싸기
    keep_null_on_exclude: bool = True
    enabled: bool = True

@dataclass
class QuerySpec:
    table: str = "eds.f_et_test"
    select_cols: list[str]
    conditions: list[Condition]
    time_range: tuple[datetime, datetime]
```

#### 토큰 문법 (auto 모드)
| 입력 | 생성 SQL |
|---|---|
| `M1 M2 M3` | `col IN ('M1','M2','M3')` |
| `M1` | `col = 'M1'` |
| `!M1 !M2` | `(col NOT IN ('M1','M2') OR col IS NULL)` |
| `M1*` | `col LIKE 'M1%'` |
| `*abc*` | `col LIKE '%abc%'` |
| `M1* M2 M3` | `(col LIKE 'M1%' OR col IN ('M2','M3'))` |
| `>=1.2`, `<0.8` | 비교 연산 |
| `1.2~3.4` | `BETWEEN 1.2 AND 3.4` |
| `"A B"` | 공백 포함 단일 값 (이스케이프 해치) |
| `#null` | `col IS NULL` |

- 입력창 옆에 파싱 결과를 **실시간 칩**으로 표시 → 유저가 규칙을 즉시 학습
- 컬럼 목록은 `DESCRIBE eds.f_et_test`로 introspect 후 JSON 캐시 (스캔 0)
- `SHOW COLUMN STATS`로 카디널리티를 알면 저카디널리티 컬럼은 체크박스 UI로 자동 전환

#### Impala 방언 주의
- 정규식 엔진은 **RE2**. lookahead/lookbehind, backreference **미지원** → 입력 즉시 검증하고 구체적 에러 표시
- REGEXP는 **부분매칭** 기본 → "완전일치" 체크박스로 `^...$` 자동 부착
- 대소문자 무시: `(?i)` 인라인 플래그 (또는 `IREGEXP`/`ILIKE`, 버전 확인 필요)
- `NOT IN`은 NULL을 조용히 제거 → 위 표대로 `OR col IS NULL` 기본 부착

#### 안전장치
- 컬럼명은 **화이트리스트 대조 후에만** 삽입 (자유입력 보간 금지)
- 값의 홑따옴표는 이중화 (`O'Brien` → `O''Brien`)
- **생성 SQL 미리보기 패널 상시 노출**
- 고급 사용자용 직접 편집 모드 제공. 단 편집 시 위젯 동기화가 끊기는 **일방향 문**임을 경고

### 4.3 청크 분할

**2단계 플래너**로 청크 크기를 균등화한다. 단순 날짜 등분은 물량이 몰린 날에 그대로 터진다.

```sql
-- 1단계: 파티션 컬럼 기준 카운트 (거의 즉시)
select dt, count(*) n from eds.f_et_test
where dt between ? and ? and <조건> group by dt
```
```python
# 2단계: 목표 행수(예: 200만)에 맞춰 greedy binning
#        한산한 날은 묶고, 폭주한 날은 시간 단위로 재분할
```

> 🔴 **파티션 프루닝이 8할이다.** `tkout_time`이 파티션 컬럼이 아닐 가능성이 매우 높다 (`dt`, `part_date`, `yyyymmdd` 등). 파티션 조건 없이 `tkout_time`만 걸면 청크마다 **풀스캔**이 돈다. `SHOW PARTITIONS eds.f_et_test` 결과가 필요하다. (§14-Q1)

```sql
where dt between '20260701' and '20260707'     -- 프루닝용, 하루 넉넉히
  and tkout_time >= '2026-07-01 00:00:00'      -- 정밀 경계
```

### 4.4 병렬 실행

```python
def fetch_chunk(chunk) -> Path:
    out = cache / f"{qspec.hash()}_{chunk.key}.parquet"
    if out.exists():
        return out                       # 재개(resume) 무료
    df = bdq.getData(chunk.to_sql())
    df = add_row_id(df)                  # key_hash 생성
    pq.write_table(to_arrow(df, schema=SCHEMA), out)   # ★ 스키마 명시
    return out
```

- `ThreadPoolExecutor(max_workers=4)` 부터. 워커 수는 설정값으로 노출
- Impala **admission control**로 인한 거절/큐잉은 **재시도 가능 예외**로 분류하고 지수 백오프
- 청크별 try/except → 실패분만 모아 "실패 N건 재시도"
- 청크는 즉시 parquet으로 떨구고 DataFrame은 폐기

#### 함정
1. **스키마 불일치** — 어떤 청크에서 컬럼이 전부 NULL이면 dtype이 object로 추론되어 나중에 parquet glob 읽기가 실패. 카탈로그 dtype으로 **명시적 pyarrow 스키마 강제**.
2. **`bdq.getData` 스레드 안전성 미확인** (§14-Q2). 안전하지 않으면 `ProcessPoolExecutor`로 전환 — 이미 parquet 경로만 반환하는 구조라 전환 비용이 낮다.
3. **Impala TIMESTAMP 타임존** — Hive/Spark가 쓴 Parquet 읽을 때 UTC 변환 옵션에 따라 9시간 밀릴 수 있음 (§14-Q3).

---

## 5. 중간 저장 — long parquet

### 왜 long인가
| | long | wide |
|---|---|---|
| 컬럼 수 | 11개 고정 | 2,000~10,000 (가변) |
| item 추가 시 | 행만 늘어남 | 스키마 변경 필요 |
| 청크 간 스키마 | **항상 동일** | 청크마다 다름 → glob 읽기 실패 |
| 리포메팅 비용 | 낮음 | 1만 컬럼 순회 |

**폭이 변하는 것은 DuckDB 테이블 하나뿐이어야 한다.**

### 키 정의 (9개)
```
root_lot_id, wafer_id, chip_x_pos, chip_y_pos,
temperature, step_id, step_seq, total_site_cnt, tkout_time
```
- `tkout_time`은 **retest 구분을 위해 키에 포함** (확정)
- `total_site_cnt`는 키에 유지 (확정)
- `key_hash = hash(위 9개)` 를 컬럼으로 저장 → dedup 조인용

### 저장 팁
- `(step_id, item_id)` 정렬 저장 → dictionary encoding 효율 상승, zone map으로 row group 스킵
- float32로 저장 (ET 계측 유효숫자에 float64 불필요, 용량 절반)

---

## 6. 리포메터

### 6.1 시트 스키마 (단일 시트)

| item_id | category | alias | cat | cat2 | cat3 | scale | offset | unit | fmt | agg | LSL | USL | formula | enabled | note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

- `category`: **`REAL`**(실측) / **`ADDP`**(계산)
- `cat`, `cat2`, `cat3`, … : `^cat\d*$` 정규식으로 **자동 수집**, 시트 등장 순서 = 계층 깊이. cat4 추가해도 코드 변경 불필요
- `agg`: summary 셀 값으로 `avg` / `std` 중 선택 (item별 지정 가능)
- `fmt`: 표시 자릿수 (`%.3g` 등). 미지정 시 유효숫자 4자리
- `enabled`: 삭제 대신 on/off (이력 보존, 실험 편의)

> ⚠️ 엑셀에서 병합된 셀은 xlwings로 읽으면 첫 칸에만 값이 온다. **forward-fill을 명시적으로 수행**할 것.

### 6.2 수식 DSL

- item 참조는 `{item_id}` 표기: `{IDSAT_N} / {IDSAT_P}`
- **ADDP를 참조하려면 시트에서 위쪽 행에 있어야 함** (행 순서 = 계산 순서) → **순환 참조가 구조적으로 불가능**, DAG/위상정렬 불필요
- 함수는 numpy 수준

#### 파싱
```python
refs = []
def sub(m):
    refs.append(m.group(1)); return f"__I{len(refs)-1}__"
safe = re.sub(r"\{([^}]+)\}", sub, formula)
tree = ast.parse(safe, mode="eval")      # eval() 절대 사용 금지
```
- 화이트리스트 방문자: `Name`, `Constant`, `BinOp`, `UnaryOp`, 등록된 `Call`만 허용
- `Attribute`, `Subscript`, `Lambda`, comprehension 전부 거부
- 에러 시 `col_offset`으로 위치까지 표시

#### AST → SQL 컴파일
```python
Name('A')     → "max(value) FILTER (WHERE item_id='A')"
BinOp(a / b)  → f"({a} / nullif({b}, 0))"        # 0 나눗셈 방어
```

> ⚠️ **함수 매핑 필수.** Python `min(a,b)`를 SQL `min()`으로 보내면 **집계 함수**가 되어 전혀 다른 결과가 나온다.
> ```python
> FUNCS = {"min": "least", "max": "greatest", "abs": "abs",
>          "sqrt": "sqrt", "log": "ln", "log10": "log10", "exp": "exp"}
> ```
> 실제 사용 함수 전체 목록 필요 (§14-Q8)

### 6.3 `Std()` 함수

```
Std({item1}, {item2}, ...)
```
`{}` 안의 item들을 **각각 하나의 포인트로 간주**하여 하나의 산포를 계산. 계산 단위는 키 8개
(`root_lot_id, wafer_id, chip_x_pos, chip_y_pos, step_id, step_seq, temperature, total_site_cnt`).

**구현: 특수 케이스가 아니라 같은 `GROUP BY`에 집계 함수를 하나 더 붙이는 것.**

```sql
SELECT <8개 키>,
  -- 일반 수식 {A}/{B}
  max(value) FILTER (WHERE item_id='A')
    / nullif(max(value) FILTER (WHERE item_id='B'), 0)      AS "RATIO",
  -- Std({A},{B},{C})
  stddev_samp(value) FILTER (WHERE item_id IN ('A','B','C')) AS "SPREAD",
  count(value)       FILTER (WHERE item_id IN ('A','B','C')) AS "SPREAD_N"
FROM v_active
WHERE item_id IN ('A','B','C')
GROUP BY <8개 키>;
```

일반 수식과 Std가 **같은 스캔·같은 쿼리 안에 공존**한다. CV(`Std/Mean`)도 공짜로 얻는다.

#### 🔴 결정 필요
| 쟁점 | 선택지 | 영향 |
|---|---|---|
| **ddof** (§14-Q5) | `stddev_pop`(numpy 기본) vs `stddev_samp`(SQL 기본, 공정 관행) | n=5에서 **11% 차이** |
| **NULL 정책** (§14-Q6) | 전부 있어야 계산 / 최소 n 이상 / 있는 것만 | 3점 산포와 2점 산포가 한 컬럼에 섞임 |
| **scale 적용 순서** (§14-Q7) | 스케일 후 수식(권장) vs 수식 후 스케일 | mA/A 혼재 시 1000배 차이 |

- `Std` 인자는 **순수 item 참조만 허용**. `Std({A}/2, {B})`는 v1에서 거부 (인자 노드가 전부 `Name`인지 검사)
- 인자들의 `unit`이 서로 다르면 검증 단계에서 경고
- `SPREAD_N`을 형제 item으로 자동 생성 → 커버리지 리포트에서 이상 탐지

### 6.4 미지원 (v1 명시적 제외)
| 유형 | 예 | 사유 |
|---|---|---|
| 키 축 이동 | `{A}@step=10 - {A}@step=5` | 결과 행의 키 정의가 흔들려 wide 적재까지 영향 |

파서가 `@` 문법을 만나면 "v1 미지원" 에러를 명확히 낸다.

### 6.5 검증 (첫 에러에서 멈추지 말고 전부 모아 표로 반환)
- `category=REAL`인데 formula 존재 / `ADDP`인데 부재
- REAL item_id가 원본에 실재하는지 (`SELECT DISTINCT item_id` 대조)
- ADDP item_id가 REAL과 충돌하거나 중복
- `{}` 짝 불일치, 미등록 함수, `Std` 인자 형식 위반
- 위쪽에 정의되지 않은 ADDP 참조 (엑셀 행번호 포함)
- `Std` 인자 unit 불일치 (경고)

### 6.6 성능
행 순서를 지키면서, **의존성이 이미 충족된 연속 행들을 자동으로 한 레벨로 묶어** 한 쿼리에 처리.
대다수 수식이 REAL만 참조하므로 실제 쿼리 수는 보통 3~5회로 수렴.

### 6.7 커버리지 리포트
```
RATIO_NP   48,912 / 50,000 (97.8%)  ✓
VT_SHIFT      312 / 50,000 ( 0.6%)  ⚠ 피연산자 커버리지 확인
```
"왜 컬럼이 비었나요" 문의의 대부분을 사전 차단한다.

---

## 7. 적재 — DuckDB wide 누적

### 7.1 왜 wide 적재인가
Spotfire·엔지니어가 **전체 item을 통째로** 요구하므로 wide 산출이 필수. (확정 요구사항)

### 7.2 재파티션 → 버킷별 피벗

블록별 처리를 위해 long parquet 전체를 매번 스캔하면 블록 수만큼 풀스캔이 발생한다. **키 해시 버킷으로 1회 재작성**하여 이를 제거한다.

```sql
COPY (
  SELECT *, hash(<9개 키>) % 512 AS bucket
  FROM read_parquet('cache/*.parquet')
) TO 'staged/' (FORMAT PARQUET, PARTITION_BY (bucket));
```

같은 키의 모든 행이 반드시 같은 버킷에 들어가므로 **경계에서 데이터가 쪼개지지 않는다.**
버킷 수 ≈ `전체 키 수 / 5,000`.

```python
for b in range(n_bucket):
    con.execute(f"""
      INSERT INTO fact BY NAME
      SELECT <9개 키>, key_hash, {PIVOT_EXPR}
      FROM read_parquet('staged/bucket={b}/*.parquet')
      GROUP BY <9개 키>, key_hash
    """)
```

#### 메모리 산식
```
피크 ≈ 블록당 키 수 × 컬럼 수 × 8B × 2
     = 5,000 × 10,000 × 8 × 2 ≈ 800MB
```
데이터가 2배가 되면 버킷 수만 2배로 올린다. 날짜 분할과 달리 **다이얼로 직접 제어**된다.

#### 필수 설정
```python
con.execute("SET memory_limit='6GB'")
con.execute(f"SET temp_directory='{cache}/spill'")   # 없으면 spill 대신 OOM
con.execute("SET preserve_insertion_order=false")
```

### 7.3 스키마 진화

```sql
BEGIN;
ALTER TABLE fact ADD COLUMN "NEW_ITEM_A" FLOAT;   -- 메타데이터 연산, 데이터 재작성 없음
ALTER TABLE fact ADD COLUMN "NEW_ITEM_B" FLOAT;   -- 한 트랜잭션에 몰아서

INSERT INTO fact BY NAME                          -- ★ 이름 매칭, 없는 컬럼은 자동 NULL
SELECT s.* FROM stg_wide s
ANTI JOIN fact f ON f.key_hash = s.key_hash;
COMMIT;
```

> **`INSERT BY NAME`이 핵심.** `INSERT INTO ... SELECT *`로 하면 컬럼이 밀려 조용히 망가진다.

추가할 컬럼 산출:
```sql
SELECT DISTINCT item_id FROM read_parquet('cache/*.parquet')
EXCEPT
SELECT column_name FROM duckdb_columns() WHERE table_name='fact';
```

### 7.4 중복 체크 (2단)

**1단 — 파일 단위 (빠른 경로)**
```sql
CREATE TABLE load_log (
  parquet_name VARCHAR PRIMARY KEY, query_hash VARCHAR,
  date_from DATE, date_to DATE, row_cnt BIGINT, loaded_at TIMESTAMP
);
```
이미 적재된 parquet은 읽지도 않는다.

**2단 — 행 단위 (정확성)**
`key_hash` ANTI JOIN. 컬럼 지향 저장이라 **dedup 검사가 `key_hash` 컬럼 하나만 스캔**하므로 1만 컬럼과 무관하게 빠르다.

- **PRIMARY KEY / UNIQUE 제약은 걸지 않는다.** DuckDB의 ART 인덱스는 대용량에서 메모리를 크게 소모하고 insert가 느려진다. `INSERT OR IGNORE`도 같이 포기.
- `key_hash`에 인덱스도 만들지 않는다. 주 1회 적재라면 컬럼 스캔 + 해시조인이 더 저렴.
- 64bit 해시 충돌 확률은 5천만 행에서 10⁻⁴ 수준. 신경 쓰이면 조인 조건에 실제 키 컬럼 하나를 AND로 추가.

### 7.5 누적 시나리오 지원

**대표 시나리오**: 일주일치 적재 → 휴가 → 복귀 후 최근 일주일 추가 적재

앱 시작 시 갭을 자동 감지하여 상단에 노출:
```
마지막 적재: 2026-08-03  |  누락 구간: 8/4 ~ 8/10 (7일)  [이 구간 추출하기]
```
중복 체크가 2단으로 있으므로 유저가 범위를 넉넉히 잡아도 안전하다.

### 7.6 뷰 체인

```sql
CREATE VIEW v_latest AS                    -- retest 최신만
SELECT * FROM fact
QUALIFY row_number() OVER (
  PARTITION BY root_lot_id, wafer_id, chip_x_pos, chip_y_pos,
               temperature, step_id, step_seq, total_site_cnt
  ORDER BY tkout_time DESC) = 1;

CREATE VIEW v_active AS                    -- exclusion 반영
SELECT l.* FROM v_latest l ANTI JOIN exclusions e USING (key_hash);

CREATE OR REPLACE VIEW v_session AS        -- 세션 조건 + 그룹
SELECT a.*, g.group_id
FROM v_active a LEFT JOIN grp g USING (root_lot_id, wafer_id)
WHERE a.root_lot_id IN (<선택 lot>)
  AND a.temperature = ? AND a.step_id = ? AND a.step_seq = ?;
```

**모든 소비자는 `v_session`만 본다.** GUI에 "재측정 포함 / 최신만" 토글 제공, 기본은 최신만.

### 7.7 운영 주의
- 컬럼 수는 **줄지 않는다.** 한 번 쓰이고 사라진 item도 영구히 남는다 → "최근 N개월 값이 없는 컬럼" 리포트를 관리 메뉴에 배치
- 1만 컬럼 테이블은 checkpoint가 무겁다 → 적재 후 명시적 `CHECKPOINT`, 파일 크기 모니터링
- 보관/파티션 정책 미정 (§14-Q17)

---

## 8. wide 산출 (CSV / SBDF)

### 8.1 행 블록 스트리밍
CSV는 행 방향 append가 공짜이므로 행 블록으로만 쪼개면 된다.

```python
ITEMS = sorted(master_item_list)          # run 시작 시 1회 스냅샷
PIVOT = ",\n".join(f'max(case when item_id=\'{i}\' then value end) as "{i}"'
                   for i in ITEMS)
for n, (lo, hi) in enumerate(blocks(total_keys, size=10_000)):
    con.execute(f"""COPY ( ... {PIVOT} ... WHERE key_id BETWEEN {lo} AND {hi} ... )
                    TO '{out}/part_{n:04d}.csv' (HEADER {n==0});""")
```

### 8.2 ⚠️ 컬럼 순서 고정
DuckDB의 `PIVOT ... ON item_id`는 **그 블록에 존재하는 값으로 컬럼을 동적 생성**한다. 어떤 블록에 item이 하나도 없으면 그 블록만 컬럼이 빠지고, 이어붙인 CSV가 **그 지점부터 한 칸씩 밀린다. 에러 없이.**

→ 반드시 **마스터 리스트 기반 명시적 `max(case when ...)` 목록**을 사용한다. 마스터 리스트는 루프 밖에서 한 번만 문자열로 생성 (SQL이 수십만 자라 매번 파싱하면 그것만으로 느려짐).

### 8.3 CSV 실무 옵션
- **유효숫자 제한**: `round(value, 6)` 또는 `printf('%.6g')` → 파일 크기 절반 이하
- **NULL**: 빈 문자열 (Spotfire/Excel 모두 정상 처리)
- **인코딩**: 한글 alias가 있으면 Excel에서 깨짐 → 첫 파트에 BOM(`\xEF\xBB\xBF`) 부착
- **압축 금지**: Spotfire가 대체로 gz를 못 읽음

### 8.4 두 벌 동시 산출
파이프라인이 이미 블록 루프이므로 추가 스캔 없이 두 벌을 뽑을 수 있다.
- `wide_full.csv` — Spotfire용, 전체 item
- `wide_{step_id}.csv` — 엔지니어용 (컬럼 수백 개로 떨어져 Excel에서 열림)

> 참고: 전체 item CSV는 step별 희소성 때문에 대부분이 빈칸이다. 50만행 × 1만컬럼이면 **구분자 콤마만으로 약 5GB**. Excel로 여는 것은 불가능하다.

### 8.5 ⚠️ 두 저장본의 의미가 다르다
| | 자동 저장본 | 조회 창 저장본 |
|---|---|---|
| 시점 | 적재 직후 | 분석 중 |
| exclusion | **미반영** (raw) | 반영 가능 |
| 성격 | 정형·재현 가능 | 일회성 탐색 |

두 파일이 섞이면 "Spotfire 값이 PPT 표와 다르다"가 발생하고 파일만 봐서는 원인을 알 수 없다.

**파일명 접두사 + 매니페스트 강제:**
```
raw_LOTA-LOTC_20260803-20260810_20260810T1432.csv
ana_split-compare_excl-58_20260810T1509.csv
+ 동명의 .json (조회조건, exclusion 반영여부, 행수, item수, 생성시각, 툴버전)
```

### 8.6 SBDF
이미 사용 중인 방식 유지. 단 **패키징 시 실기 검증 필수** — SBDF 라이브러리는 C 확장을 포함하는 경우가 많아 PyInstaller가 자동 수집하지 못한다 (`--hidden-import` / `--collect-all` 필요 가능성).

---

## 9. 분석 세션

### 9.1 모델
```python
@dataclass
class AnalysisSession:
    version: int = 1
    lots: list[str]
    fixed_dims: dict[str, str]              # temperature, step_id, step_seq
    groups: dict[str, Group]
    assign: dict[tuple[str, str], str]      # (lot, wafer) → group_id
    ref_group_id: str | None = None
    exclusions: ExclusionSet
```

### 9.2 조회 UI — 캐스케이딩
lot 선택 → temperature 목록 갱신 → step_id 갱신 → step_seq 갱신.
각 단계가 상위 선택으로 필터되므로 **빈 결과가 나오는 조합을 만들 수 없다.**

```sql
SELECT DISTINCT temperature FROM fact WHERE root_lot_id IN (<선택>);
```

다중 lot일 때 fixed_dims 후보는 **선택된 lot 전부에 존재하는 값(교집합)** 만 표시.

조회 결과 요약 상시 표시: `3 lots / 112 wafers / 41,203 rows / 2,891 items`

**다중 lot 커버리지 경고:**
```
LOT_A  62 wafers  2,891 items
LOT_B  25 wafers  2,847 items   ⚠ LOT_A 대비 44개 item 없음
```
lot마다 PDK 버전이나 test program이 다르면 item 집합이 갈린다. 경고 없이 표를 만들면 유저가 "데이터가 안 뽑혔다"로 오해한다.

### 9.3 그룹

```python
@dataclass
class Group:
    id: str; name: str
    color: str          # hex only
    symbol: str         # 논리명 (circle/square/triangle/diamond/cross)
    size_pt: float      # ★ point 단위 (§11.1)
    order: int          # 범례·표 정렬
    visible: bool = True
```

- **색·심볼·크기는 세션 소유.** 한 페이지에 slot이 6개인데 plot마다 그룹 색이 다르면 그 페이지는 읽을 수 없다.
- 기본 팔레트는 **색약 안전 8색**(Okabe-Ito). fab 리포트는 흑백 출력이 잦으므로 **심볼을 색과 독립된 보조 채널**로 자동 배정.
- ref 그룹은 검정/회색 자동 배정.
- 미배정 wafer는 LEFT JOIN으로 NULL → GUI에 `미분류 12장` 상시 표시 + "제외 / 회색 표시" 토글.

#### 배정 방법
1. **규칙 초기화**: `lot별 자동 그룹` (기본), wafer_id 범위 등
2. **엑셀 붙여넣기** (주력)
3. **수동 보정**: 좌측 lot/wafer 트리 + 우측 그룹 리스트, 드래그 이동

#### 붙여넣기 설계
클립보드는 TSV 텍스트(`QApplication.clipboard().text()`). **즉시 적용이 아니라 미리보기 표로 받는다.**

```
┌─ 붙여넣은 내용 (34행) ──────────────────────────┐
│ [lot ▼]   [wafer ▼]  [group ▼]  [무시 ▼]        │  ← 컬럼 역할 드롭다운
│ A1234.1  │ 01       │ Split_A  │ 2026-08-03     │
└──────────┴──────────┴──────────┴────────────────┘
 ☑ 첫 행은 헤더    ☑ 빈 셀은 위 값 상속    [적용]
```

- 컬럼 역할을 드롭다운으로 고르게 하면 **어떤 포맷이든 수용** 가능. 헤더명(`lot`/`랏`/`group`/`그룹`)으로 자동 추정 후 유저는 확인만
- `wafer` 컬럼이 없으면 → 해당 lot 전체 wafer를 그 그룹에 (lot 단위 비교의 기본 케이스)
- **그룹 순서 = 붙여넣은 등장 순서** (범례·표 컬럼 순서로 직결. 알파벳 정렬 금지)
- 색 컬럼이 없으면 팔레트 자동 배정. **엑셀 셀 배경색은 클립보드 텍스트에 실리지 않음** → 색 지정은 별도 컬럼 필요 (툴팁 안내)
- 역방향도 제공: 우클릭 → "현재 배정을 클립보드로 복사" (TSV 조립 한 번, 사실상 공짜)

#### 붙여넣기 함정
| 함정 | 대응 |
|---|---|
| 앞자리 0 소실 (`01`→`1`) | 정확 매칭 → zero-pad → 대소문자 무시 순 시도. fuzzy 매칭 시 **"W1 → W01로 해석 (24건)"** 표시 |
| 엑셀 병합 셀 | "빈 셀은 위 값 상속" 기본 ON |
| 후행 공백 / `\xa0` | `strip()` + 치환 |
| `\r\n`, 마지막 빈 줄 | 빈 행 제거 |

#### 검증 (적용 전 전부)
- DB에 없는 lot → 목록 표시 후 무시 여부 확인
- 같은 (lot, wafer)가 두 그룹 → **에러로 차단** (마지막 우선 같은 암묵 규칙 금지)
- 조회 조건에 없는 lot → "3개 lot이 조회 목록에 추가됩니다" 안내 후 자동 편입
- 미배정 wafer 개수 표시

### 9.4 ref

DuckDB 안의 베스트 웨이퍼 또는 실험비교군이므로 **그룹 중 하나를 ref로 지정**하면 충분하다. 별도 `RefSpec` 불필요.

```python
ref_group_id: str | None
```

- 델타: `value - avg(value) FILTER (WHERE group_id = ref_group)`
- best wafer(1~2장) / 실험비교군(여러 장) 모두 **같은 메커니즘**
- 붙여넣기에 포함되므로 "ref lot이 조회 대상에 없음" 문제가 자동 소멸
- ref 통계는 반드시 `v_session`에서 산출 (같은 fixed_dims·같은 exclusion 보장). 별도 쿼리로 빼면 25℃ 그룹을 125℃ ref와 비교하는 사고가 난다

#### v1 범위
| 용도 | v1 |
|---|---|
| plot 기준선 (μ±3σ 밴드) | ✅ |
| plot 강조 (별도 색·심볼) | ✅ |
| 테이블 델타 컬럼 | ✅ |
| **값 정규화** (`value/ref_μ`) | ⏸ 별도 토글로 후속 — 축 라벨·단위·규격선이 전부 따라 바뀜 |

### 9.5 Exclusion (전역)

포인트 제외는 plot뿐 아니라 **summary 통계에도 반영**된다 (확정). 따라서 plot 소유가 아니라 데이터셋 레이어 소유다.

```sql
CREATE TABLE exclusions (
  key_hash UBIGINT, scope VARCHAR,      -- 'point' | 'die' | 'wafer'
  reason VARCHAR, rule_id VARCHAR, ts TIMESTAMP, actor VARCHAR
);
```
- base parquet은 불변 유지. 되돌리기는 row 삭제 한 번
- 개별 제외: `sigClicked` + `pointsAt()`
- 영역 제외: RectROI/lasso로 잡되 **좌표가 아니라 predicate로 저장** → 데이터가 바뀌어도 재현
- 제외 점은 삭제하지 말고 **회색 빈 원**으로 표시 + undo

#### 🔴 결정 필요 (§14-Q12)
어떤 die의 값이 튀어 제외할 때 **그 die의 다른 item 측정값도 전부 빼야 하는가?**
웨이퍼 단위 이상치면 die 전체, 계측 노이즈면 그 point만. 둘 다 필요할 것으로 예상 → 우클릭 메뉴 "이 점만 / 이 die 전체 / 이 wafer 전체".

#### 파생 item 전파
`C = A / B`에서 A의 한 point를 제외하면 C도 비어야 한다.
→ **exclusion은 파생 연산 이전에 적용**되어야 한다. `v_active`에서 뽑으면 `MAX(CASE...)`가 NULL을 내어 자동 해결된다. 순서가 뒤집히면 "지웠는데 통계가 안 변하는" 현상이 발생한다.

#### 감사 추적
보고서 숫자가 사람의 판단에 따라 달라지므로:
- summary 셀에 `N=1,842 (-58)` 형태로 제외 수 병기
- 덱 마지막에 **exclusion 이력 슬라이드 자동 생성** (누가/언제/무슨 룰로/몇 개)

---

## 10. Summary 테이블

### 10.1 표 형태
```
┌──────┬───────┬────────────┬──── LOT_A ────┬─ LOT_B ─┐
│ cat  │ cat2  │  alias     │ W01 │W02 │W03 │ W01 │W02│
├──────┼───────┼────────────┼─────┼────┼────┼─────┼───┤
│      │       │ Idsat N SVT│12.3 │12.5│12.1│ 11.9│...│
│ NMOS │ Idsat │ Idsat N LVT│15.2 │15.4│15.0│ 14.8│   │
│      ├───────┼────────────┼─────┼────┼────┼─────┼───┤
│      │  Vt   │ Vtlin N SVT│0.451│0.44│... │     │   │
├──────┼───────┼────────────┼─────┼────┼────┼─────┼───┤
│ PMOS │  ...  │            │     │    │    │     │   │
└──────┴───────┴────────────┴─────┴────┴────┴─────┴───┘
```
- 행 병합(cat 계층)과 **열 병합(lot 계층)이 대칭**
- lot 헤더 병합은 필수 — 여러 lot을 뽑으면 `W01`이 중복되어 구분 불가

### 10.2 스펙
```python
@dataclass
class TableSpec:
    items: list[str]
    fixed_dims: dict[str, str]          # ★ 세션에서 상속, 필수
    value: Literal["avg", "std"] = "avg"   # 시트 agg 컬럼으로 item별 오버라이드 가능
    column_by: Literal["wafer", "group"] = "wafer"
    show_delta_vs_ref: bool = False
    orientation: Literal["item_rows"] = "item_rows"
```

### 10.3 계산
셀 값 = **한 wafer 안 모든 die에 대한 집계**. `std`를 고르면 자연스럽게 within-wafer 산포가 되어 §6.3 `Std()` 정의와 의미가 일치한다.

```sql
SELECT item_id, root_lot_id, wafer_id,
       avg(value) AS avg_v, stddev_samp(value) AS std_v, count(value) AS n
FROM (UNPIVOT (SELECT ... FROM v_session WHERE <필터>)
      ON <선택 item> INTO NAME item_id VALUE value)
GROUP BY 1,2,3;
```

**wafer 피벗은 SQL에서 하지 않는다.** wafer 목록이 쿼리마다 달라 동적 SQL이 지저분해지고, 결과가 기껏해야 수천 셀이라 Python에서 레이아웃하는 편이 훨씬 통제하기 쉽다.

### 10.4 ⚠️ 차원 고정 필수
group by에 `temperature`, `step_id`, `step_seq`가 없으면 **-40℃와 125℃가 한 셀에서 평균**된다. 값은 나오지만 무의미하다.

- GUI에서 미지정이면 **생성 차단**, 고유값 목록을 보여주고 선택 유도
- 고유값이 하나뿐이면 자동 확정
- **표 캡션에 조건 명기** (`@ 25℃, step M1`) — 표만 떼어 다른 자료에 붙이는 일이 흔하다

### 10.5 정렬 (알파벳 금지)
| 대상 | 규칙 |
|---|---|
| cat 순서 | 시트 등장 순서 (NMOS→PMOS→Cap 의도 보존) |
| item 순서 | cat 안에서 시트 행 순서 |
| wafer 순서 | **자연 정렬** (`W1, W2, W10`). 문자열 정렬이면 `W1, W10, W2` |
| lot 순서 | tkout_time 순 |

### 10.6 병합 알고리즘
```python
for lvl in range(n_cat):
    key = lambda r: tuple(r.cats[:lvl+1])       # ★ 상위 레벨 포함이 핵심
    for _, run in groupby(rows, key=key):
        rs = list(run)
        if len(rs) > 1:
            tbl.cell(rs[0].y, lvl).merge(tbl.cell(rs[-1].y, lvl))
```
상위 레벨 키를 포함하지 않으면 서로 다른 cat1에 속한 동명의 cat2가 잘못 이어붙는다.
병합 셀 텍스트는 세로 가운데 정렬(`MSO_ANCHOR.MIDDLE`).

### 10.7 ⚠️ 크기 — 1/6 슬롯 한계
lot 10개 × wafer 25장 = **데이터 컬럼 250개**. 슬롯 하나에 물리적으로 불가능.

| 초과 대상 | 대응 |
|---|---|
| wafer 컬럼 과다 | lot당 슬롯 분리 / lot 평균 1컬럼 축약 옵션 |
| item 행 과다 | **cat1 그룹 하나 = 슬롯 하나** (기본 규칙) |

실측 가독 한계: **행 10~12개 × 데이터 열 8~10개**. 초과 시 **생성 전에** "슬롯 3개로 분할됩니다" 안내.

`cat1당 슬롯 자동 분할`을 기본으로 두면 엔지니어가 시트에서 cat만 잘 나눠도 덱 구성이 자동으로 된다.

### 10.8 표현
- 값 없는 셀은 **빈칸** (`0`이나 `-`는 실제 값으로 오독)
- `n`이 임계 미만인 셀은 회색 (wafer에 die 2개 남았는데 std를 찍으면 안 됨)
- 규격(LSL/USL) 이탈 셀 붉은 배경 — wafer 방향 경향이 한눈에 보여 효과가 특히 크다
- 숫자 우측 정렬 / 라벨 좌측. 소수 자릿수를 item별로 고정하면 자릿수가 세로로 정렬됨
- 단위는 헤더로 올리고 가수만 표시하는 옵션 (`IDSAT [µA]` → `12.34`)

### 10.9 Cp/Cpk (§14-Q11 확인 필요)
시트의 `LSL`/`USL`/`TARGET` 컬럼 사용.
```
Cpk = min( (USL-μ)/(3σ), (μ-LSL)/(3σ) )
```
- 한쪽 규격만 있으면 단측 계산
- 규격 없는 item은 **0이 아니라 빈칸** (0은 최악 공정으로 오독됨)
- σ=0 이거나 n이 작으면(예: n<30) 값 대신 경고 표시

---

## 11. Plot

### 11.1 스펙
```python
@dataclass
class AxisSpec:
    field: str
    scale: Literal["linear", "log", "symlog"] = "linear"
    range: tuple[float, float] | None = None      # None = auto. ★ 항상 데이터 공간
    label: str | None = None
    nonpositive: Literal["mask", "clip"] = "mask"

@dataclass
class PlotSpec:
    version: int = 1
    x: AxisSpec
    y: AxisSpec
    color_by: str = "group_id"      # 세션 그룹이 그냥 하나의 컬럼
    facet_by: str | None = None
    show_ref_band: bool = False
    title: str | None = None
```
전체가 JSON 직렬화 가능해야 한다. **"이 plot 레시피"를 저장해 다음 달 데이터에 재적용**하는 것이 이 툴의 실질 가치.

### 11.2 렌더러 2개, 스펙 1개
| | 용도 | 라이브러리 |
|---|---|---|
| 화면 | 탐색·편집 | **PyQtGraph** (100만 점, ROI 마킹, 축 링크, GraphicsLayout trellis) |
| PPT | 최종 산출 | **matplotlib** (벡터 품질, 레이아웃 제어) |

### 11.3 ⚠️ 단위 통일 — 가장 많이 깨지는 지점
| | 단위 |
|---|---|
| PyQtGraph `size` | **픽셀** |
| matplotlib `s` | **point²** (면적) |

**Theme에는 point로만 저장**하고 렌더러가 각자 변환한다.
```python
pyqtgraph_size = size_pt * screen_dpi / 72     # → 픽셀
matplotlib_s   = size_pt ** 2                   # → 면적
```

**심볼 문자도 다르다.** 삼각형 `'t'`(pg) vs `'^'`(mpl), 마름모 `'d'` vs `'D'`.
```python
SYMBOL = {"circle": ("o","o"), "square": ("s","s"),
          "triangle": ("t","^"), "diamond": ("d","D"), "cross": ("+","+")}
```
색은 **hex만** 사용 (두 라이브러리의 색 이름 집합이 다름).

### 11.4 ⚠️ 로그 축
PyQtGraph는 log 모드에서 데이터를 log10으로 변환해 그리므로 `viewRange()`가 **로그 공간 값**을 반환한다. matplotlib `set_xlim`은 데이터 공간이다.
→ **스펙에는 항상 데이터 공간(`10**v`)으로 정규화 저장.** 안 맞추면 PPT 축 범위가 조용히 틀어진다.

log 축에서 0/음수 처리 정책을 스펙에 명시하고, 마스킹된 점 개수를 GUI에 표시한다.

### 11.5 포인트 제외
- **인덱스가 아니라 `key_hash`로 관리.** 행 인덱스는 재쿼리·재필터하면 전부 깨진다
- 클릭 = 단일, 드래그 = 영역, **"제외 취소" 모드도 같은 비중으로**
- 제외 점은 회색 빈 원으로 남기고 undo 제공
- ⚠️ **전역 반영을 알려야 한다.** 첫 제외 시 안내 + 도크 제외 카운터 갱신 + summary 탭 "재계산 필요" 배지

### 11.6 축 편집 UX
축 우클릭 메뉴: `로그/선형` · `범위 자동/수동` · **`현재 화면 범위로 고정`**
세 번째가 실사용 빈도가 가장 높다 (마우스로 확대 → 버튼 하나로 잠금).

### 11.7 렌더러 규율
- **Theme 밖에서 색·크기·폰트 숫자 하드코딩 금지.** 테스트로 강제
- 미리보기와 export는 **같은 함수**를 쓴다
  ```python
  def render_mpl(spec, theme, w_in, h_in, dpi) -> Figure   # 단 하나
  ```
- **골든 이미지 회귀 테스트**: 대표 스펙 10개(로그축, 제외점, 그룹 4개, facet, 빈 그룹, 단일점 …)를 고정하고 빌드마다 두 렌더러 결과를 PNG diff. 사람 눈 검수는 3주면 안 하게 된다

---

## 12. PPT 생성

### 12.1 덱 구조
```python
@dataclass
class SlotSpec:
    page: int
    position: int          # 1~6, reading order → row=(p-1)//3, col=(p-1)%3
    content_ref: str
    kind: Literal["plot", "table"]

@dataclass
class ReportSpec:
    version: int = 1
    slots: list[SlotSpec]
    plots: dict[str, PlotSpec]
    tables: dict[str, TableSpec]
```
- 한 페이지 최대 **2×3 = 6 슬롯**
- 템플릿(엑셀)에서 page/position을 지정받음

### 12.2 python-pptx 실무
- **좌표 하드코딩 금지.** .pptx 템플릿에 6개 빈 picture placeholder 레이아웃을 만들고 `slide.placeholders[idx]`로 채운다 → 배치 조정을 개발자 없이 가능
- **핵심 트릭**: placeholder 크기를 EMU로 읽어 inch로 변환 후 matplotlib `figsize`로 전달. 안 하면 crop-to-fill로 축이 잘린다
  ```python
  w_in = ph.width / 914400; h_in = ph.height / 914400
  fig = Figure(figsize=(w_in, h_in), dpi=250)
  ```
- **빈 placeholder 제거**: `ph._element.getparent().remove(ph._element)` (안 하면 "그림을 추가하려면 클릭" 프롬프트가 남음)
- **small-multiple 전용 테마**: 1/6 크기에서 기본 폰트는 읽히지 않는다. tick 6~7pt, label 8pt, 마커 축소, 범례는 페이지당 1개 공유
- `pyplot` 대신 `matplotlib.figure.Figure` + `FigureCanvasAgg` 직접 사용 (워커 스레드 안전, 메모리 누수 없음)
- **절대 금지**: 위젯 `grab()` 스크린샷을 PPT에 삽입. 화면 해상도·종횡비 종속이라 빔프로젝터에서 깨진다

### 12.3 표 스타일
- 기본 테이블 스타일 해제 (`tbl.first_row=False`, `tbl.horz_banding=False`) 후 직접 채색
- **컬럼 폭 자동 없음** → 내용 길이로 계산해 명시 지정
- **⚠️ 테두리 API 없음** → `a:lnL/lnR/lnT/lnB`를 XML로 직접 삽입. 헬퍼 함수 필수 (없으면 확실히 촌스러워짐)
- 헤더는 진한 배경 + 흰 볼드
- 폰트 자동 축소는 **6pt 하한**, 초과 시 축소가 아니라 분할

### 12.4 사전 검증
템플릿 파싱 직후 한 번에 모아 리포트:
- (page, position) 중복 / position > 6
- 존재하지 않는 itemid 참조
- 슬롯 크기 초과 예상 (§10.7)

> 30장짜리 덱을 20장쯤 만들다 죽는 것이 최악의 UX다.

---

## 13. 애플리케이션 구조

### 13.1 화면 구성
```
┌──────────────┬────────────────────────────────┐
│ 조회 조건    │  [추출/적재] [Summary] [Plot]  │
│  lot 3개     │                                │
│  25℃ / M1    │   탭 = 산출물 종류             │
├──────────────┤                                │
│ 그룹 (4)     │                                │
│  ■ Split_A   │                                │
│  ■ Split_B   │                                │
│  ● REF       │                                │
├──────────────┤                                │
│ 제외 58점    │                                │
│ [이력 보기]  │                                │
└──────────────┴────────────────────────────────┘
        ↑ 도크 = 공유 상태 (세션)
```

**탭 = 산출물 / 도크 = 공유 상태 / 별도 창 = 파이프라인 밖 도구**

세션(조회조건·그룹·ref·exclusion)은 summary와 plot이 **공유**하므로 탭 안에 두면 안 된다. 도크에 상시 노출하면 탭 전환 시 컨텍스트가 유지되고, exclusion 변경이 즉시 시각화된다.

### 13.2 조회 창 (별도)
파이프라인 밖 탐색 도구. 모든 사용자에게 필요하지 않으므로 분리한다.

- **읽기 전용 커넥션 필수**
  ```python
  con = duckdb.connect(db_path, read_only=True)
  ```
  유저는 언젠가 `DELETE`/`DROP`을 붙여넣는다. 정규식 검사는 반드시 뚫린다. 엔진 레벨 차단이 유일하게 안전하며, 메인 앱의 쓰기 커넥션과 동시 오픈도 가능해진다
- 추출 탭의 조건 빌더(콤보박스 + 토큰 UI)를 **재사용**, 하단에 "SQL 직접 편집" 토글
- 미리보기는 `LIMIT 1000`, 저장은 `COPY (...) TO`로 스트리밍
- 자주 쓰는 조회는 이름 붙여 세션 JSON에 저장

### 13.3 상태 관리
```python
session.apply(cmd)  →  signal  →  모든 뷰 재렌더
```
위젯이 스펙을 고치면서 캔버스를 따로 갱신하는 코드가 하나라도 있으면 "화면엔 반영됐는데 PPT엔 안 된" 버그가 반드시 발생한다. **캔버스를 직접 만지는 코드는 렌더러 안에만 존재**해야 한다.

### 13.4 복원 (두 가지 다른 기능)

**(a) Undo/Redo** — `QUndoStack` + `QUndoCommand` (PySide6 내장). 편집 하나 = 커맨드 하나. Ctrl+Z와 히스토리 패널이 자동으로 따라온다. 포인트 제외 작업에서는 필수.

**(b) 저장/불러오기** — 3층 분리
| 층 | 내용 | 범위 |
|---|---|---|
| Theme | 팔레트, 폰트, 기본 크기 | 앱 전역 |
| Session | 조회조건, 그룹, ref, exclusion | 분석 단위 |
| ReportSpec | 덱 구성 + Plot/TableSpec | 리포트 단위 |

- 전부 JSON, **`version` 필드를 첫날부터**. 스키마는 반드시 바뀌고, 그때 유저 파일이 안 열리면 신뢰를 잃는다
- **자동 저장 필수** — 포인트 제외는 30분 걸리는 작업이 되기도 한다. 크래시로 날리면 그 툴은 두 번 다시 사용되지 않는다. N분마다 temp 스냅샷
- 수동 배정 그룹은 lot이 바뀌면 재적용 불가(wafer 키 불일치) → 가능하면 **규칙으로 저장**하고, 수동분은 "8건 매칭 실패, 재배정 필요"로 정직하게 안내

### 13.5 스레드 모델
- 추출/적재/생성은 **워커 스레드**에서, 진행률은 Signal로
- DuckDB는 **단일 writer**. 워커는 parquet만 떨구고 적재는 메인에서 직렬 수행
- xlwings(COM)는 **STA 제약** → 워커 스레드에서 호출 금지. 리포메터는 앱 시작 시 메인 스레드에서 1회 읽고 JSON 캐시로 전환

### 13.6 패키징
- PyInstaller **`--onedir`** (`--onefile`은 시작이 느리고 COM 궁합이 나쁨)
- 난독화가 필요하면 Nuitka
- **xlwings는 대상 PC에 Excel 설치가 전제** — 배포 범위 확인 필요 (§14-Q25)
- SBDF 라이브러리 C 확장 수집 확인 (§8.6)
- **한글 폰트**: matplotlib은 기본 폰트에 한글이 없어 □□□로 깨진다. 폰트 파일을 번들하고 `font_manager`에 등록할 것 (§14-Q22)

---

## 14. 🔴 미결 쟁점

### A. 조사·확인 필요 (외부 사실)

| # | 쟁점 | 왜 중요한가 | 확인 방법 |
|---|---|---|---|
| Q1 | **`eds.f_et_test`의 파티션 컬럼** | 파티션 프루닝 실패 시 청크마다 풀스캔. 성능이 자릿수 단위로 갈림 | `SHOW PARTITIONS eds.f_et_test` |
| Q2 | **`bdq.getData` 스레드 안전성** | 안전하지 않으면 병렬 구조 전면 변경 (Process pool) | 2스레드 동시 호출 테스트 |
| Q3 | **Impala TIMESTAMP 타임존 오프셋** | 9시간 밀리면 날짜 청크와 리포트 전체가 어긋남 | 알려진 lot의 tkout_time을 MES와 대조 |
| Q4 | **step별 item 중복도(희소성)** | 버킷 수·블록 크기 산정 근거. wide CSV 크기 예측 | `select step_id, count(distinct item_id) group by 1` |
| Q26 | **대상 PC RAM** | 블록 크기·`memory_limit` 기본값 | — |
| Q25 | **배포 대상 PC에 Excel 설치 여부** | xlwings 전제 조건. 없으면 리포메터 읽기 경로 변경 필요 | — |

### B. 의미 결정 필요 (설계 확정 불가 항목)

| # | 쟁점 | 선택지 | 영향 |
|---|---|---|---|
| Q5 | **`Std()` ddof** | 모집단(numpy 기본) / 표본(SQL 기본, 공정 관행) | n=5에서 11% 차이. 리포트 숫자에 직결 |
| Q6 | **`Std()` NULL 정책** | 전부 있어야 / 최소 n 이상 / 있는 것만 | 3점 산포와 2점 산포가 한 컬럼에 섞임 |
| Q7 | **scale 적용 순서** | 수식 전(권장) / 수식 후 | mA·A 혼재 시 1000배 차이 |
| Q8 | **numpy 함수 목록** | 실제 사용 함수 전체 | SQL 매핑 테이블 확정 |
| Q12 | **exclusion 단위** | point / die / wafer, 또는 전부 지원 | 계측 노이즈 vs 웨이퍼 이상치로 의미가 다름 |
| Q13 | **retest 기본 표시** | 최신만(권장) / 전체 포함 | 통계에 retest 중복 반영 여부 |
| Q11 | **Cp/Cpk 필요 여부** | 필요 / 불필요 | 시트에 LSL/USL 컬럼 추가 여부 |
| Q9 | **summary `std`의 의미** | within-wafer(현 설계) / wafer-to-wafer / total σ | 현 표 형태(wafer 컬럼 피벗)에서는 within-wafer로 자동 결정됨 — 이것이 의도인지 확인 |
| Q10 | **wafer 컬럼 초과 시** | lot당 슬롯 분리 / lot 평균 축약 / 유저 선택 | 250컬럼은 슬롯에 불가능 |
| Q28 | **미배정 wafer 기본 동작** | 제외 / 회색 표시 | — |
| Q29 | **ref 델타 기본값** | 항상 표시 / 옵션 | — |

### C. 환경·운영 결정

| # | 쟁점 | 비고 |
|---|---|---|
| Q16 | **DuckDB 파일 위치** — 개인 로컬 / 공유 네트워크 드라이브 | 🔴 **매우 중요.** 공유면 단일 writer 제약이 다중 사용자 문제로 직결되고 네트워크 I/O로 성능도 크게 달라짐. 로컬 권장 |
| Q24 | **동시 사용자 수 / DB 공유 여부** | Q16과 연동 |
| Q17 | **데이터 보존 정책** | 무한 누적이면 언젠가 연/분기 파티션 필요. 컬럼도 줄지 않음 |
| Q15 | **자동 저장 범위** | 이번 추출분만 / 적재된 전체 |
| Q23 | **배포·업데이트 방식** | 네트워크 드라이브 복사 / 자동 업데이트 여부 |
| Q30 | **감사 로그 요구사항** | 사내 배포툴 기준 확인 |

### D. 미정의 스펙 (설계 필요)

| # | 쟁점 | 비고 |
|---|---|---|
| Q18 | **plot 종류** | scatter만? trend(시계열), box, histogram, wafer map 필요 여부 |
| Q19 | **facet/trellis 필요 여부** | "Spotfire 수준 자유도"의 범위 확정 |
| Q27 | **plot 템플릿 엑셀 시트 스키마** | page/position 외에 x축 item, y축 item, 축 스케일, 제목 등을 어떻게 받을지 미정 |
| Q20 | **PPT 사내 표준 템플릿** | 존재 여부 및 입수 |
| Q21 | **슬라이드 제목·캡션 규칙** | 조건 자동 표기 형식 |
| Q22 | **한글 폰트 번들** | matplotlib 한글 깨짐 대응 |

---

## 15. 착수 순서 제안

1. **Q1~Q4 확인** — 이게 안 되면 §4·§7 수치가 전부 추정치로 남는다
2. **Q5~Q7 결정** — 리포메터 구현의 전제
3. 수직 슬라이스 프로토타입: 소량 lot 1개로 `추출 → long parquet → 리포메팅 → 버킷 피벗 → DuckDB 적재` 까지 관통
4. `PlotSpec` + 렌더러 2종 + **골든 이미지 테스트 기반 마련** (초기에 깔지 않으면 나중에 못 깐다)
5. 세션/그룹/exclusion
6. summary·PPT 조립
7. 패키징 및 실기 검증 (xlwings, SBDF, 한글 폰트)
