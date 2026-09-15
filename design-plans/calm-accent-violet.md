# 차분한 보라 액센트 — 화면을 "계측기 콘솔"에서 "조용한 제품"으로 한 단계 더

Written against: 2026-09-06 작업 트리. 이 문서는 2026-09-05
`instrument-console-redesign.md`의 **후속**이다. 그 문서가 '라이트 크롬 + 종이
측정면 + 단일 인디고 액센트'를 결정했고, 이 문서는 사용자 설계 인터뷰 3문에서
나온 세 답이 그 결정을 어떻게 다듬는지 기록한다.

## 사용자 설계 인터뷰 (이 문서의 근거)

**Q1. 거슬리는 것 (A·B·C·D 중 선택)** — 답: **A·C·D 전부 + B 일부**
- A '무채색 회색 크롬이 거슬림' — 크롬이 색기가 없어 "회색 통"으로 보임
- B '입력 컨트롤이 브라우저 기본값처럼 보임' — 일부 동의: **입력(필드)에만**
  해당, 콤보는 이미 아이콘·모서리로 다듬어져 있음
- C '섹션 그룹핑·카드가 없어 정보 밀도가 낮음' — 동의
- D '버튼 끼리 위계가 없어 전부 버튼 벽' — 동의

**Q2. 방향** — 답: **방향 1 - 현대 제품형** (브랜드 색 있는 상태 표시줄,
라이브러리·캔버스·도구 모음, 잉크가 풍부한 차트, 컴팩트하지만 뭉개지지 않은
레이아웃, 크롬에 색기 있는 중성면)
**Q3. 액센트 색** — 답: **A: 차분하고 신뢰감 있는 보라 `#6D5BD0`** (딥 인디고는
레거시 공정관리 툴 냄새, 파랑은 Okabe-Ito·타깃 파랑과 겹침, 다른 보라 후보는
채도 과함/대비 부족).

세 답이 만나는 지점: **"크롬에 색기 있는 중성면"(Q2) + 차분한 보라(Q3)**. 이
문서는 그 만남을 "함께 달라지는 토큰 8개"로 굳힌다.

## Evidence chain

- Surface: `src/etreport/ui/theme.py:28-84`(TOKENS) — 색·형태·글자 크기의
  단일 진실. `style.qss`는 `%TOKEN%`만 쓴다. `tests/test_theme.py`가
  `CONTRAST_PAIRS`(theme.py:87-107)로 WCAG AA(4.5:1)를 검사한다.
- Problem: 사용자가 세 번에 걸쳐 "크롬이 회색 통처럼 보인다"를 반복했다
  (인터뷰 Q1-A). 크롬 토큰 `INK #F6F7F9`는 순수 무채색에 가깝고, 액센트
  `ACC #3E51C4`는 레거시 공정관리 툴 계열 인디고다. UI만의 색 정체성이
  부족하다.
- Design evidence:
  - Q1-D: 도크 버튼은 `analysis_ws.py:113-118`에서 **세 개가 모두
    `GhostButton`**으로 만들어진다 — 저장/새 이름/삭제가 색으로 구분되지
    않는다. 저장은 주 동작인데 보조 버튼과 같은 겉모습이다.
  - Q1-C: `data_ws.py`는 4개의 `_chrome_section()`(설정·대상·기간·조회 조건,
    `data_ws.py:237/249/265/280`)을 **구분선 없는 크롬 면**에 잇따라 놓는다.
    섹션 경계가 hairline 하나뿐이라 정보 밀도가 낮아 보인다.
  - `cards.py:10` — `QGraphicsDropShadowEffect`가 이미 임포트되어 있고,
    `GhostButton`(`cards.py:150-158`)이 그림자 효과를 **꺼 둔 채** 쓰는
    이유("효과가 켜져 있으면 위젯의 사각 실루엣이 둥근 모서리 바깥으로
    삐져나와 버튼 오른쪽에 회색 얼룩이 남는다")가 기록되어 있다 — 카드에
    그림자를 상시로 걸면 같은 함정에 걸린다.
- Owner: 색·형태·글자 크기는 `ui/theme.py`가 소유한다. 위젯 구조는
  `analysis_ws.py`·`data_ws.py`·`widgets/cards.py`가 소유한다.
- Scope: theme.py 8개 토큰, style.qss(포커스 링·danger 버튼·sectionGroup),
  cards.py(Card 그림자), analysis_ws.py(도크 버튼 위계), data_ws.py
  (sectionGroup 래핑). WARN·ERR·OK·신호색은 **건드리지 않는다** — 차트가
  Okabe-Ito 8색과 규격 빨강을 이미 쓰므로 UI가 같은 색조로 경쟁하면 안 된다.
- Uncertainty: 카드 그림자(섹션 2)는 QGraphicsDropShadowEffect가
  `GhostButton`에서 실측으로 얼룩을 남긴 기능이다. 카드는 불투명 배경이고
  버튼은 투명 배경이라 그림자 원본 알파가 달라 결과가 다를 수 있다 —
  캡처로 검증하고 얼룩이 나오면 카드 그림자를 버린다(Stop conditions).

## Design decision

다섯 조각이 하나의 "차분한 보라"를 이룬다. 각 조각은 독립적으로 승인할 수
있도록 섹션으로 나눈다.

### 섹션 1 — 색 토큰 8개 교체

토큰 하나하나를 바꾸지 않고 8개가 **같은 방향**(크롬에 보라 틴트, 액센트는
보라)으로 함께 움직인다. 대비는 전부 WCAG AA를 유지한다(계산값은 아래 표).

| 토큰 | 현재 | 새 값 | 이유 |
|---|---|---|---|
| `ACC` | `#3E51C4` | `#6D5BD0` | 인터뷰 Q3-A. 흰 글자 5.18:1 |
| `ACC_H` | `#3341A8` | `#5A49B8` | hover 어두운 쪽. 흰 글자 6.82:1 |
| `ACC_SOFT` | `#EEF0FB` | `#F2F0FC` | 선택 항목 배경. ACC 위 4.60:1 |
| `ACC_LINE` | `#A9B3E8` | `#B9B0E8` | 액센트 hairline (대비 무관·비텍스트) |
| `ACC_INK` | `#3E51C4` | `#6D5BD0` | 크롬 위 액센트 글자. INK 위 4.79:1 |
| `INK` | `#F6F7F9` | `#F5F6F9` | 크롬 바탕. **극미한** 보라 틴트 |
| `INK_3` | `#E9EBEE` | `#E9EAF0` | 크롬 hover 바탕 틴트 |
| `INK_LINE` | `#E1E3E8` | `#E1E2E9` | 크롬 hairline 틴트 |

계산 (WCAG 상대 휘도, 0.5 반올림): 주 버튼 `PAPER/ACC` **5.18:1** ✓,
`PAPER/ACC_H` **6.82:1** ✓, 선택 항목 `ACC/ACC_SOFT` **4.60:1** ✓,
크롬 액센트 `ACC_INK/INK` **4.79:1** ✓, 크롬 보조 `INK_MUTED/INK`
**5.23:1** ✓, 크롬 본문 `INK_TEXT/INK` **15.15:1** ✓,
`INK_TEXT/INK_2` **16.37:1** ✓. (염료 라인 `ACC_LINE 1.86~2.01:1`은
비텍스트 UI 컴포넌트 — AA 대상 아님.)

`INK_2(#FFFFFF)`·`INK_TEXT`·`INK_MUTED`·`PAPER`·`CANVAS`·`FIELD`·`RULE`·
`WARN/ERR/OK` 계열은 **바꾸지 않는다**. 특히 `INK_2`는 "크롬 위 입력면"으로
종이와 같은 흰색을 유지해야 "흰 종이 = 측정면뿐" 원칙이 깨지지 않는다.

### 섹션 2 — 카드에 상시 그림자 (입체감)

`Card`(`widgets/cards.py:19`)에 `QGraphicsDropShadowEffect`를 **기본값으로**
건다. blur 14, offset y=2, 색 검정 알파 ~30. 크롬 요소(도크·파일 행·상태
레일)는 **평평하게 둔다** — 그림자는 신뢰를 주는 측정면(카드·표)만 띄운다.

`GhostButton`이 이미 같은 효과 인프라(blur/offset 애니메이션)를 갖고 있으므로
새 프리미티브가 아니라 같은 패턴을 재사용한다. **정적(effectsEnabled 상시)**
그림자이므로 애니메이션 객체가 필요 없다.

주의: `GhostButton` 주석(`cards.py:155-157`)에 "효과가 켜져 있으면 사각
실루엣이 삐져나온다"가 있다. 그 함정은 **버튼이 투명 배경이어서** 그림자가
버튼 경계 사각형을 그대로 그림자로 삼는 경우다. `Card`는 불투명 흰 배경 +
QSS `border-radius`이므로 원본 알파가 둥근 모서리를 따라가 **둥근 그림자가
되어야 한다**. 검증은 캡처로 한다(아래 Validation).

### 섹션 3 — 입력 컨트롤 포커스 링 (인터뷰 Q1-B)

`QLineEdit`·`QComboBox`·`QDateEdit`·`QAbstractSpinBox`·`QPlainTextEdit`의
focus 상태를 1px `%ACC%` 테두리에서 **2px `%ACC%` 포커스 링 + 패딩 1px 보정**으로
바꾼다. 패딩을 함께 줄여 2px 테두리로 내용이 1px씩 밀리지 않게 한다.
체크박스·라디오의 `indicator`도 focus/hover 시 `%ACC%` 테두리 링을 준다 —
키보드로 어느 필드에 있는지 1px 테두리로는 흐릿하다.

범위는 **입력 컨트롤**로 한정한다(인터뷰 Q1-B "일부 동의"). 콤보는 이미
아이콘·모서리로 다듬어져 있으므로 동일한 포커스 링을 주되 그 **모양**은
바꾸지 않는다.

### 섹션 4 — 버튼 위계 (인터뷰 Q1-D)

도크의 세 버튼(`analysis_ws.py:113-118`)을 위계로 가른다:

- **저장** — 주 동작. `primary` 채움 버튼(`%ACC%` 배경, 흰 글자)
- **새 이름** — 보조. `GhostButton` 유지
- **삭제** — 위험 동작. `danger` 변형: `%ERR%` 테두리·글자, hover 시
  `%ERR_SOFT%` 배경

`danger`는 새 QSS 규칙 `QPushButton[danger="true"]`로 추가한다. 값은 기존
`ERR/ERR_SOFT` 토큰만 쓴다 — 신호색을 새로 만들지 않는다.

### 섹션 5 — 데이터 화면 섹션 그룹핑 (인터뷰 Q1-C)

`data_ws.py`의 4개 `_chrome_section()`(설정·대상·기간·조회 조건)을
`#sectionGroup` 컨테이너로 감싼다. `#sectionGroup`은 `%INK_2%` 배경 +
`%INK_LINE%` hairline 테두리 + `%RADIUS_LG%` 모서리. 크롬 위의 "올린 표면"이므로
`INK_2`(크롬 계열)를 쓰고 `PAPER`(측정면)를 쓰지 않는다 — "흰 종이 = 측정면"
원칙 유지.

카드로 만들지 않는 이유: 분석 도크와 다른 언어를 쓰기 시작하면 화면이
"카드 앱"이 된다. `INK_2` 위에 hairline이 있는 "패널"은 카드보다 한 단계
차분하다 — 이 화면의 주인공은 카드가 아니라 진행 로그와 추출이다.

## Reuse

- `GhostButton._shadow`(`cards.py:150-158`) — 그림자 인프라. 카드 버전은
  애니메이션 없이 상시 켠다.
- `ERR/ERR_SOFT` 토큰 — danger 버튼. 새 신호색을 만들지 않는다.
- `INK_2/RADIUS_LG/INK_LINE` — `#sectionGroup` 패널.
- `%ACC%` — 포커스 링. 새 토큰을 만들지 않는다.

새 프리미티브는 `#sectionGroup`(오브젝트 이름)과 `QPushButton[danger="true"]`
(QSS 속성 선택자)뿐. 둘 다 새 토큰이 아니라 기존 토큰을 받는 선택자다.

## Changes

1. `src/etreport/ui/theme.py`
   - Change: 8개 토큰 값을 위 표로 교체. `CONTRAST_PAIRS`는 그대로 —
     값이 바뀌어도 쌍은 동일하다. 주석(`theme.py:53-57` "액센트는 인디고"
     문단)도 보라로 갱신한다.
   - Verify: `myenv/bin/python -m pytest tests/test_theme.py -q` 통과.

2. `src/etreport/ui/style.qss`
   - Change: 입력 컨트롤 focus 2px 링 + 패딩 보정, `indicator` focus/hover
     링, `QPushButton[danger="true"]` 규칙 추가, `#sectionGroup` 규칙 추가.
   - Preserve: `%TOKEN%` 참조만 사용(hex 직입 금지), 전역
     `QWidget { background: … }` 금지, Fusion 고정.
   - Verify: `myenv/bin/python -m pytest tests/test_theme.py tests/test_hover_states.py -q`.

3. `src/etreport/ui/widgets/cards.py`
   - Change: `Card.__init__`에 상시 `QGraphicsDropShadowEffect`(blur 14, y
     offset 2, 알파 30). 애니메이션 없음.
   - Verify: 캡처에서 카드가 둥근 그림자를 지고, 사각 실루엣 얼룩이 없다.
     얼룩 확인 시 Stop conditions.

4. `src/etreport/ui/analysis_ws.py`
   - Change: 도크 버튼(113-118행) — 저장에 `setProperty("primary", True)`
     (또는 QSS가 이미 갖는 선택자에 맞춤), 삭제에 `setProperty("danger",
     True)`. 새 이름은 그대로.
   - Verify: 저장만 채워진 버튼, 새 이름은 투명, 삭제는 빨간 테두리.

5. `src/etreport/ui/data_ws.py`
   - Change: 4개 `_chrome_section()` 호출부를 `#sectionGroup` 컨테이너로
     감싼다. WIDE_BREAKPOINT 2열 배치가 그대로 유지되도록 그리드 셀 구조는
     건드리지 않는다.
   - Verify: 데모 캡처에서 4개 패널이 구분되어 보인다.

## Scope

- Inherit: theme.py 토큰은 앱 전역(화면·PPT·xlsx·수동). 토큰 값 교체는
  전 화면에 동시에 퍼진다.
- Verify: `tests/test_theme.py`(대비·치환 누락)·`tests/test_hover_states.py`
  (버튼 픽셀이 TOKENS와 일치)·`tests/test_ux_redesign.py`(크롬 색 규칙).
- Exclude:
  - WARN·ERR·OK·신호색 전부 — 차트와 경쟁하지 않는 원칙 유지
  - 콤보 팝업·달력(QPalette 자리) — 토큰 교체로 자동 따라간다
  - 분석 도크의 섹션을 카드로 만드는 것(섹션 5 참조)
  - PPT·xlsx·표의 색 — 렌더러가 토큰을 읽지 않는 자리는 범위 밖

## Validation

- Product: `--demo` 캡처에서 (1) 크롬이 무채색이 아니라 은은한 보라쪽,
  (2) 카드가 살짝 떠서 도크와 구분되고, (3) 입력 필드에 포커스 링이 보이고,
  (4) 도크 버튼이 세 위계로 갈리고, (5) 데이터 화면에 4개 패널이 보인다.
- Interface:
  - 화면: [데이터]·[분석] 양쪽. 분석은 탐색/요약/리포트 탭 전부.
  - 상태: hover(버튼 리프트)·focus(포커스 링)·disabled(희석)가 토큰과 일치
  - 뷰포트: 기본 1500×940과 도크 폭 줄인 상태에서 `#sectionGroup` 패널이
    잘리지 않는지(`DOCK_WIDTH` 규칙 — `test_ux_redesign.test_dock_content_fits_its_fixed_width`)
- System: `grep`로 hex가 `ui/theme.py` 바깥에 새로 박히지 않았는지. 새 색
  (보라)은 토큰 안에만 있다.
- Repository: `myenv/bin/python -m pytest` 전체. `tools/make_manual.py`
  재캡처 후 `data/summary` 전후 대조.

## Stop conditions

- 카드 그림자에서 사각 실루엣 얼룩이 캡처로 확인되면 **(섹션 2)을 버린다** —
  나머지 4섹션은 진행하고, 그림자는 카드가 아닌 다른 방법(예: 경계선 두께
  강화)으로 대체하지 않는다. 같은 기능이 버튼에서 실측으로 실패한 전적이
  있으므로 카드에서 되는지가 확인 전까지 채택으로 치지 않는다.
- `ACC_SOFT` 대비가 4.5 미만으로 떨어지는 값이 나오면 다시 고른다 —
  선택 항목 배경은 텍스트가 얹히는 면이라 AA를 반드시 지킨다.
- `test_hover_states.py`가 TOKENS와 어긋나면 수치를 다시 조정한다 — 버튼
  픽셀 계약은 토큰 표와 한 몸이다.
- 사용자가 "카드에 그림자가 더 많아야/더 적어야" 한다고 하면 그 한 섹션만
  조정한다 — 나머지는 확정으로 진행한다.

## Design documentation

- 이 문서가 곧 결정 기록이다. 승인된 섹션 값은 테스트(대비·hover 픽셀·
  도크 폭)가 계약으로 지킨다.
- `docs/ui-mockup-v12.html`·`design-plans/instrument-console-redesign.md`는
  수정하지 않는다 — 후자는 이 문서의 부모로 남는다.

## 관련 플랜

- `instrument-console-redesign.md` — 라이트 크롬·단일 액센트 원칙의 부모
  문서. 이 문서는 그 원칙 안에서 색 방향만 다듬는다.
- `dirty-button-state-color.md`·`ghost-button-disabled-state.md` — 같은
  `style.qss` 버튼 규칙을 다루지만 각각 dirty(주황)·disabled(희석) 상태이고
  이 문서는 도크 **버튼 위계**(primary/danger)를 다룬다.