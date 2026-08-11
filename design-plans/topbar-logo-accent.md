# 상단바 로고의 강조를 굵기에서 액센트 색으로 되돌린다

Written against: unavailable — 이 저장소는 Git 작업 트리가 아니다(`git rev-parse` 실패). 2026-08-11 시점의 작업 트리 기준으로 작성했다.

## Evidence chain

- Surface: `src/etreport/ui/mainwindow.py:43-45` (상단바 로고 라벨) + `src/etreport/ui/style.qss:7` (`#logo` 규칙). 앱의 모든 화면에 항상 떠 있는 유일한 브랜드 마크다.
- Problem: 코드는 `QLabel("ET <b>Report</b>")`로 "Report"를 **굵기**로 강조한다. `style.qss:7` `#logo { font-size:14px; }`는 `color`도 `font-weight`도 지정하지 않으므로, "Report"는 `style.qss:2`의 `QWidget{color:#1d1d1f}`를 그대로 상속받아 본문과 같은 검정으로 렌더된다. 결과적으로 상단바에 액센트 색(`#0071e3`)이 전혀 나타나지 않는다.
- Design evidence:
  - `docs/ui-mockup-v12.html:390` `<div class="logo">ET <em>Report</em></div>`
  - `docs/ui-mockup-v12.html:37` `.logo em{font-style:normal;color:var(--acc)}` — 강조 구간을 **굵기가 아니라 액센트 색**으로 구분한다(`font-style:normal`로 기울임을 명시적으로 끈 것도 "색만으로 강조한다"는 의도를 드러낸다)
  - `docs/ui-mockup-v12.html:36` `.logo{font-size:14.5px;font-weight:600;...}` — 라벨 **전체**가 600이고, `em`은 그 위에 색만 얹는다
  - `style.qss:11` `#wsButton:checked { background:#eaf3fd; color:#0071e3; }` — 액센트 값 `#0071e3`은 이미 이 파일에서 강조 수단으로 쓰이고 있다
- Owner: 색과 굵기는 `src/etreport/ui/style.qss`가 소유한다. 라벨의 구조는 `src/etreport/ui/mainwindow.py`가 소유한다.
- Scope and affected surfaces: `src/etreport/ui/mainwindow.py`의 상단바 1곳. 다른 소비자는 없다(`grep -rn 'logo' src/` → `mainwindow.py:43-45`와 `style.qss:7`뿐).
- Uncertainty: **이 플랜은 세 건 중 유일하게 근거가 목업 대조에만 의존한다.** 사용자가 목업 v12는 현재 코드보다 뒤처져 있고 그동안 코드 쪽에서 갱신해 왔다고 확인해 주었으므로, `<b>` 강조가 **의도적인 코드 쪽 변경일 가능성을 배제하지 못한다**. 다만 `style.qss:7`이 `#logo` 규칙을 만들어 두고 `font-size`만 지정한 채 색·굵기를 비워 둔 형태는 재설계보다 이식 누락에 가깝고, `mainwindow.py:43`이 `<b>`를 쓴 것 자체가 "Report를 강조한다"는 의도는 코드에도 남아 있음을 보여준다. 쟁점은 강조 **여부**가 아니라 **수단**이다. 사용자가 이 변경을 지시했으므로 진행하되, 되돌릴 근거가 나오면 Stop conditions를 따른다.

## Design decision

로고를 QLabel 하나의 리치 텍스트에서 **두 개의 QLabel**로 나누고, 강조 구간에 `#logoAccent` 오브젝트 이름을 주어 색을 `style.qss`가 소유하게 한다. 동시에 `#logo`에 목업 `:36`의 `font-weight:600`을 더한다.

두 라벨로 나누는 이유: Qt의 QSS 선택자는 QLabel **안쪽의** 리치 텍스트 조각(`<b>`, `<span>`)을 겨냥할 수 없다. 한 라벨을 유지하려면 `mainwindow.py`에 `<span style="color:#0071e3">`처럼 색을 인라인으로 박아야 하고, 그러면 액센트 값이 `style.qss` 바깥에 복제된다. `CLAUDE.md:49-51`이 "같은 규칙을 두 곳에 두지 않는다"를 이 코드베이스의 설계 축으로 못 박고 있으므로, 색은 토큰 소유자 안에 남긴다.

## Reuse

- `#0071e3` — `style.qss:11`(`#wsButton:checked`), `:35`, `:39`, `:44`가 이미 쓰는 액센트 값
- `style.qss:7`의 기존 `#logo` 규칙 — 새 규칙을 만들지 않고 여기에 `font-weight`를 더한다
- Exemplar: `src/etreport/ui/mainwindow.py:57-59` (`db_pill`에 `setObjectName`을 주고 표현은 QSS에 맡기는 이 파일의 기존 패턴)

새 프리미티브는 필요 없다. `#logoAccent`는 새 오브젝트 이름이지만 새 토큰이 아니라 기존 액센트 값을 받는 선택자다.

## Changes

1. `src/etreport/ui/mainwindow.py`
   - Change: 43-45행의 단일 라벨을 간격 0인 컨테이너 안의 두 라벨로 바꾼다.
     ```python
     logo = QWidget()
     lg = QHBoxLayout(logo)
     lg.setContentsMargins(0, 0, 0, 0)
     lg.setSpacing(0)
     for text, name in (("ET ", "logo"), ("Report", "logoAccent")):
         lab = QLabel(text)
         lab.setObjectName(name)
         lg.addWidget(lab)
     h.addWidget(logo)
     ```
   - Preserve: 로고가 상단바 레이아웃의 **첫 위젯**이라는 위치, 바로 뒤의 `h.addSpacing(14)`(`mainwindow.py:46`), 표시 문자열 "ET Report"와 그 사이 공백. 상단바의 `h.setSpacing(10)`이 두 조각 사이에 끼어들지 않도록 컨테이너 레이아웃의 `setSpacing(0)`을 반드시 유지한다. `QWidget`은 `mainwindow.py:5-13`에서 이미 임포트돼 있으므로 임포트를 추가하지 않는다.
   - Verify: 상단바에 "ET Report"가 한 덩어리로(글자 사이 벌어짐 없이) 보이고 "Report"만 파란색이다.

2. `src/etreport/ui/style.qss`
   - Change: 7행 `#logo   { font-size:14px; }`를 아래로 교체한다.
     ```
     #logo, #logoAccent { font-size:14px; font-weight:600; }
     #logoAccent { color:#0071e3; }
     ```
   - Preserve: `font-size:14px`. 목업 `:36`은 14.5px이지만 현재 코드의 14px를 유지한다 — 이 플랜의 근거는 강조 **수단**이지 크기가 아니고, 사용자가 목업이 코드보다 뒤처졌다고 확인했으므로 대조만으로 크기를 바꾸지 않는다. 6행 `#topbar` 규칙은 건드리지 않는다.
   - Verify: "ET"와 "Report"가 같은 크기·같은 굵기(600)로, 색만 다르게 보인다.

## Scope

- Inherit: 상단바 로고 1곳. 앱의 모든 화면에서 항상 보인다.
- Verify: 상단바 전체 정렬 — 로고를 컨테이너로 감싸면서 세로 중앙 정렬이나 좌측 여백(`h.setContentsMargins(18, 8, 18, 8)`, `mainwindow.py:41`)이 흐트러지지 않았는지. 바로 옆 [데이터]/[분석] 버튼과의 간격도 확인한다.
- Exclude:
  - `#logo`의 `font-size`를 목업의 14.5px로 맞추는 것 — 위 Preserve 참조
  - 목업 `:36`의 `letter-spacing:-.02em` — Qt QSS는 `letter-spacing`을 지원하지 않는다
  - `#wsButton`의 굵기(비활성 500 vs 현재 600), `#topbar` 좌우 패딩(24px vs 18px) 등 목업과의 다른 차이 — 근거가 목업 대조뿐이고 사용자가 목업의 후행을 확인했으므로 다루지 않는다
  - `#dbPill`의 값 강조(목업 `:44` `.db-pill b`) — 목업은 숫자 하나만 굵게 하는데 `mainwindow.py:96`은 숫자가 둘이라 어느 쪽인지 결정되지 않는다. 범위 밖이다.

## Validation

- Product: 앱을 열었을 때 상단바가 제품의 브랜드 색을 드러낸다. 문구는 "ET Report"로 읽혀야 한다.
- Interface:
  - 화면: [데이터] / [분석] 두 워크스페이스 모두에서 상단바 확인(`mainwindow.py:63-68`의 QStackedWidget 양쪽)
  - 상태: `_switch(0)`/`_switch(1)` 전환 시 로고가 흔들리지 않는지
  - 뷰포트: 기본 창(`mainwindow.py:30` `resize(1500, 940)`)과 창을 가로로 크게 줄였을 때 — 로고 컨테이너가 압축되어 "ET"와 "Report"가 잘리지 않는지
  - 폰트: `fonts.py`가 고르는 한글 폰트가 바뀌어도(예: Malgun Gothic ↔ Noto Sans KR) 두 조각의 베이스라인이 어긋나지 않는지
- System: `#0071e3`이 `style.qss` 안에만 있고 `mainwindow.py`로 복제되지 않았는지 확인한다 — 이 플랜에서 두 라벨 구조를 택한 이유가 바로 그것이다.
- Repository: `grep -rn '0071e3' src/etreport/ui/*.py` → **결과 없음**. `grep -n 'logo' src/etreport/ui/style.qss` → `#logo, #logoAccent` 1줄과 `#logoAccent` 1줄.

## Stop conditions

- `<b>` 강조가 의도적인 코드 쪽 결정이었다는 근거가 나오면 중단한다 — 이 플랜은 세 건 중 근거가 가장 약하고, 유일하게 목업 대조에만 서 있다.
- 두 라벨 분리 후 상단바 정렬이 틀어지고 `setSpacing(0)`·마진 조정으로 복구되지 않으면 중단한다. 로고 하나 때문에 상단바 레이아웃을 재구성할 만한 사안이 아니다.
- 액센트를 `#0071e3` 이외의 값으로 바꿔야 한다는 요구가 생기면 중단한다 — 브랜드 색을 정하는 결정이며 별도 범위다.

## Design documentation

- 수용·검증 후: 별도 문서화 없음. 이 변경은 기존 액센트 토큰의 사용처를 하나 늘리는 것이고 새 규칙을 만들지 않는다.
- `docs/ui-mockup-v12.html`은 수정하지 않는다.

## 관련 플랜

- `ghost-button-disabled-state.md`, `dirty-button-state-color.md` — 같은 `style.qss`를 수정하지만 각각 29-35행 구간을 다루고 이 플랜은 7행을 다루므로 충돌하지 않는다.
