# Ghost 버튼이 비활성 상태를 시각적으로 드러내게 한다

Written against: unavailable — 이 저장소는 Git 작업 트리가 아니다(`git rev-parse` 실패). 2026-08-11 시점의 작업 트리 기준으로 작성했다.

## Evidence chain

- Surface: `src/etreport/ui/style.qss` (전역 스타일시트, `src/etreport/app.py:9-18` `_load_style()`가 `QApplication.setStyleSheet()`으로 앱 전체에 적용) → 소비 표면은 `src/etreport/ui/widgets/cards.py:49-53`의 `GhostButton`을 쓰는 모든 화면
- Problem: 비활성화된 ghost 버튼이 활성 상태와 완전히 동일하게 렌더된다. `style.qss:29`가 `QPushButton:disabled { background:#c4cdd6; color:#ffffff; }`로 비활성 표현을 선언하지만, 뒤에 오는 `style.qss:32-34` `QPushButton[ghost="true"]`가 `background`와 `color`를 다시 덮어쓴다. Qt QSS 명시도(`qcssparser`)는 속성 선택자와 의사상태를 같은 자리(각 `0x10`)로 계산하므로 두 규칙은 동점이고, 동점일 때는 **나중에 선언된 규칙이 이긴다**. ghost 규칙에는 `:disabled` 변형이 없어서 비활성 시 배경(`#ffffff`)·글자색(`#1d1d1f`)·테두리가 그대로 남는다. `:hover`는 비활성 위젯에서 발생하지 않으므로, 정적으로 활성/비활성을 구분할 단서가 하나도 없다.
- Design evidence:
  - `style.qss:29` — 이 스타일시트 자신이 QPushButton의 비활성 표현을 이미 정의하고 있다. 즉 "비활성은 눈에 보여야 한다"는 결정은 이 파일 안에 이미 존재하며, ghost 변형에만 도달하지 못하고 있다. **이 플랜의 근거는 목업 대조가 아니라 스타일시트 내부의 모순이다.**
  - `docs/ui-mockup-v12.html:63` `.btn:disabled{opacity:.4;cursor:default;transform:none}` — 목업에서도 이 규칙은 `.btn.ghost`(`:60`)에 함께 걸리므로 ghost 버튼의 비활성이 구분된다. (보조 근거. 목업은 현재 코드보다 뒤처져 있다 — Uncertainty 참조.)
- Owner: `src/etreport/ui/style.qss` (버튼 표현의 단일 소유자). 비활성 상태를 세우는 쪽은 각 워크스페이스이며 그 동작은 이미 올바르다 — 이 플랜은 **동작을 바꾸지 않고 표현만 바꾼다**.
- Scope and affected surfaces:
  - `src/etreport/ui/data_ws.py` — `GhostButton` 9개. 그중 `data_ws.py:320-321` `self.btn_cancel = GhostButton("중지")`는 **생성 직후 `setEnabled(False)`** 이고 추출 중에만 활성화된다(`data_ws.py:487` 활성, `:517`·`:522` 비활성). 즉 [데이터] 워크스페이스를 열면 항상 보이는 [중지] 버튼이 누를 수 없는 상태로 누를 수 있게 보인다 — 이 결함의 대표 사례다.
  - `src/etreport/ui/analysis_ws.py` — `GhostButton` 12개(`:109, :121, :143, :146, :162, :181, :191, :521, :708, :778, :780, :907, :1004`)
  - `src/etreport/ui/widgets/reformatter_dialog.py:48,60`, `sql_dialog.py:83`, `group_dialog.py:101` — `setProperty("ghost", True)`를 직접 세우는 버튼들도 같은 규칙을 받는다
- Uncertainty: Qt의 QSS 명시도 동점 처리를 소스 규칙으로 추론했고 화면으로 확인하지 않았다. 아래 Validation의 데모 실행으로 **먼저 결함을 재현한 뒤** 수정한다. 재현되지 않으면 Stop conditions를 따른다.

## Design decision

`style.qss`의 ghost 규칙 블록 뒤에 `QPushButton[ghost="true"]:disabled` 규칙을 추가하고, **`style.qss:29`가 이미 쓰고 있는 비활성 값을 그대로 재사용한다**.

이 선택자의 명시도는 `1 + 0x10(속성) + 0x10(의사상태) = 0x21`로, `QPushButton[ghost="true"]`(`0x11`)와 `QPushButton:disabled`(`0x11`) 양쪽을 모두 이긴다. 따라서 선언 순서에 의존하지 않고 결정적으로 적용된다.

규칙 순서를 바꾸는 방식(예: `:disabled`를 ghost 뒤로 이동)은 채택하지 않는다. `QPushButton[dirty="true"]`(`style.qss:30-31`)와의 상대 순서까지 함께 바뀌어 의도하지 않은 상태 조합에 영향을 주기 때문이다. 규칙 추가는 기존 순서를 전혀 건드리지 않는다.

새 색을 만들지 않는다. 이 결함은 "비활성 표현이 없다"가 아니라 "이미 있는 비활성 표현이 ghost에 도달하지 못한다"이므로, 해결은 기존 값을 도달시키는 것이다.

## Reuse

- `style.qss:29`의 비활성 값 — `background:#c4cdd6; color:#ffffff`
- `style.qss:32-34`의 ghost 테두리 값 — `border:1px solid rgba(0,0,0,.14)` (비활성 규칙에서 재선언하지 않고 상속시킨다)
- Exemplar: `src/etreport/ui/style.qss:29` (기본 버튼의 비활성 표현), `src/etreport/ui/style.qss:35` (`[ghost="true"]:hover` — ghost에 상태 변형을 붙이는 기존 패턴)

새 프리미티브는 필요 없다.

## Changes

1. `src/etreport/ui/style.qss`
   - Change: 35행 `QPushButton[ghost="true"]:hover { background:#f7f8fa; border-color:#0071e3; }` **바로 다음 줄**에 아래를 추가한다.
     ```
     QPushButton[ghost="true"]:disabled { background:#c4cdd6; color:#ffffff; }
     ```
   - Preserve: 26-35행의 기존 규칙 텍스트와 **선언 순서를 그대로 둔다**. `QPushButton:disabled`(29), `[dirty="true"]`(30-31), `[ghost="true"]`(32-34), `[ghost="true"]:hover`(35)를 이동·수정·삭제하지 않는다. ghost 버튼의 `border`·`font-weight:500`·`padding:6px 12px`는 32-34행에서 상속되어야 하므로 새 규칙에서 재선언하지 않는다.
   - Verify: `myenv/bin/python app.py --demo` 실행 → [데이터] 워크스페이스에서 하단 실행 푸터의 [중지]가 회색 배경·흰 글자로 보이고, 옆의 [추출하고 적재]나 다른 ghost 버튼([저장], [새 설정], [최근 7일] 등)과 명확히 구분된다.

제품 소스(`.py`)는 이 플랜에서 수정하지 않는다.

## Scope

- Inherit: `GhostButton`(`cards.py:49-53`)을 쓰는 모든 버튼과 `setProperty("ghost", True)`를 직접 세우는 버튼 전부 — `data_ws.py` 9개, `analysis_ws.py` 12개, `reformatter_dialog.py` 2개, `sql_dialog.py` 1개, `group_dialog.py` 1개
- Verify: `data_ws.py:320-321` `btn_cancel`(기본 비활성 — 가장 눈에 띄는 변화), `analysis_ws.py:146` `btn_cache`와 `:191` `btn_undo`(조건부 비활성 가능성이 있는 버튼들 — 활성 시 외형이 이전과 동일한지 확인)
- Exclude:
  - 기본 버튼(`QPushButton` 비-ghost)의 비활성 표현 — 이미 `style.qss:29`로 정상 동작한다
  - `QPushButton[dirty="true"]`의 색 — 별도 플랜(`dirty-button-state-color.md`)에서 다룬다
  - 어떤 버튼을 언제 비활성화할지에 대한 결정 — 동작 영역이며 현재 구현이 옳다
  - `setProperty("ghost", True)`(불리언)와 `setProperty("dirty", "true")`(문자열)의 표기 불일치 — 양쪽 모두 Qt에서 정상 매칭되며 화면에 영향이 없다

## Validation

- Product: [데이터] 워크스페이스를 연다. 추출을 시작하기 전 [중지]는 누를 수 없는 상태이며, 그 사실이 외형으로 드러나야 한다. 추출을 시작하면 [중지]가 정상 ghost 외형으로 돌아오고 누를 수 있어야 한다.
- Interface:
  - 상태: ghost 활성 / ghost 비활성 / ghost hover / 기본 버튼 비활성 — 네 상태가 서로 구분되는지
  - 화면: [데이터] 실행 푸터, [분석] 좌측 도크(`analysis_ws.py:143,146,162,181,191`), 리포트 탭 슬롯 버튼(`analysis_ws.py:1004`)
  - 내용 극단: `data_ws.py:187`의 `✕` 조건 삭제 버튼처럼 `setFixedWidth(30)`인 짧은 ghost 버튼에서도 배경색이 정상으로 채워지는지
- System: `#c4cdd6`가 `style.qss` 안에서 비활성 표현의 단일 값으로 유지되는지 확인한다. ghost 전용 비활성 색을 새로 만들지 않았어야 한다.
- Repository: `grep -n 'ghost\|disabled' src/etreport/ui/style.qss` → `QPushButton:disabled`(1건), `QPushButton[ghost="true"]`(1건), `[ghost="true"]:hover`(1건), `[ghost="true"]:disabled`(1건, 신규)가 이 순서로 나온다.

## Stop conditions

- 데모 실행에서 **수정 전에** [중지]가 이미 비활성으로 구분되어 보이면 중단한다 — 명시도 추론이 틀렸다는 뜻이므로, 실제 렌더 결과를 근거로 이 플랜을 다시 작성해야 한다.
- 수정 후 활성 상태의 ghost 버튼 외형이 달라지면 중단한다 — `:disabled` 규칙이 의도보다 넓게 걸렸다는 뜻이다.
- ghost 버튼의 비활성 표현을 `#c4cdd6` 이외의 값으로 바꿔야 한다는 요구가 생기면 중단한다. 그것은 새 토큰을 만드는 결정이고 이 플랜의 범위가 아니다.

## Design documentation

- 수용·검증 후: `src/etreport/ui/style.qss`의 새 규칙 옆에 한 줄 주석으로 남긴다 — `/* ghost는 :disabled 변형이 없으면 기본 비활성 규칙을 덮어쓴다 */`. 이 파일은 주석으로 의도를 남기는 기존 관행이 있다(`style.qss:1`).
- `README.md`·`CLAUDE.md`·목업은 이 플랜에서 수정하지 않는다.
