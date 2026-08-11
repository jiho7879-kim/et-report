# 재계산 필요(dirty) 버튼 색을 데이터 계열 팔레트에서 분리한다

Written against: unavailable — 이 저장소는 Git 작업 트리가 아니다(`git rev-parse` 실패). 2026-08-11 시점의 작업 트리 기준으로 작성했다.

## Evidence chain

- Surface: `src/etreport/ui/style.qss:30-31` (전역 스타일시트, `src/etreport/app.py:9-18` `_load_style()`가 앱 전체에 적용) → 소비 표면은 [분석] 워크스페이스의 명시적 재계산 버튼 3종
- Problem: `style.qss:30-31`은 dirty 상태색으로 `#ff9500`/hover `#ffa01a`를 쓴다. 그런데 **`#ff9500`은 이 코드베이스에서 이미 데이터 계열(plot group) 색으로 쓰이고 있다** — `src/etreport/ui/widgets/group_dialog.py:28`의 `"System"` 팔레트 첫 번째 색이 `#ff9500`이다. `CLAUDE.md:59-62`에 따르면 화면 캔버스도 `render/mpl_renderer.py`로 그리므로, 그룹 1번 색이 칠해진 그래프 바로 옆에서 [그리기] 버튼이 같은 `#ff9500`으로 켜진다. 하나의 색이 "이 그룹의 데이터"와 "이 버튼을 눌러야 한다"는 서로 무관한 두 의미를 동시에 지시한다.
- Design evidence:
  - **코드 내부 충돌(주 근거, 목업과 무관):** `group_dialog.py:28` `"System": ["#ff9500", "#0071e3", "#34c759", ...]` vs `style.qss:30` `QPushButton[dirty="true"] { background:#ff9500; }`. 같은 리터럴이 계열색과 상태색으로 이중 사용된다. 이 팔레트의 두 번째 색 `#0071e3`은 액센트 토큰과 겹치지만 그쪽은 "선택/활성"이라는 일관된 의미를 유지하는 반면, `#ff9500`은 의미가 갈린다.
  - **역할 분리의 선례:** `docs/ui-mockup-v12.html`은 두 역할을 명시적으로 분리한다. 계열색 `#ff9500`(`:1142, :2279, :2286, :2452, :2469`)과 "저장 안 됨 / 다시 계산해야 함" 신호 `#c26c00`(`:64` `.btn.warn`, `:65` hover `#d47700`, `:112` `.preset-bar .dirty-dot`, `:189` `.tab .dirty`, `:232` `.cfg-preset .dot`) — 상태색은 네 곳에서 일관되게 `#c26c00`이다.
  - **이식이 한쪽만 끝난 정황:** `group_dialog.py:28`의 6색은 목업 `:2279`의 배열과 **순서까지 완전히 동일**하다. 즉 계열 팔레트는 목업에서 그대로 가져왔는데 상태색만 다른 값이 되었다. 이는 의도적 재설계보다 이식 누락에 가깝다.
  - **수용된 결정:** `CHANGELOG.md:45-46` "지연 계산: Summary [표 만들기] · 탐색 [그리기] · 리포트 [미리보기]. 변경되면 **버튼이 주황색**으로", `CLAUDE.md:67-70` 동일 내용. "주황색"이라는 의미는 출시된 결정이며 이 플랜이 유지한다 — `#c26c00`도 `#ff9500`도 주황 계열이므로 이 변경은 문서화된 동작을 바꾸지 않는다.
- Owner: `src/etreport/ui/style.qss` (상태색의 단일 소유자). dirty 속성을 세우는 쪽(`analysis_ws.py`)은 수정하지 않는다.
- Scope and affected surfaces: `src/etreport/ui/analysis_ws.py`의 세 버튼 —
  - `btn_apply` — `:325` dirty 설정, `:351` 해제
  - `btn_draw` — `:585`/`:642`, `:967`/`:1124` (탐색·리포트 두 경로)
  - `btn_build` — `:731`/`:833`
  `CLAUDE.md:68-70`이 규정한 지연 계산 트리거 버튼 전부이며, 다른 곳에서 `dirty` 속성을 쓰는 위젯은 없다(`grep -rn 'dirty' src/etreport/ui/` 결과 8건이 모두 위 세 버튼과 QSS 2줄).
- Uncertainty: **목업 v12는 현재 코드보다 뒤처져 있다(사용자 확인).** 따라서 "목업이 `#c26c00`이므로 코드가 틀렸다"는 논증만으로는 부족하다. 이 플랜이 서 있는 근거는 목업이 아니라 코드 내부의 색 이중 사용이며, `#c26c00`은 저장소 전체에서 상태색으로 문서화된 **유일한** 값이라서 대체값으로 택했다. `#ff9500`이 dirty 색으로 의도적으로 선택된 것이었다면 Stop conditions를 따른다.

## Design decision

`style.qss:30-31`의 dirty 배경색을 `#c26c00`, hover를 `#d47700`으로 바꾼다.

이렇게 하면 `#ff9500`은 데이터 계열색이라는 하나의 의미만 갖게 되고(`group_dialog.py:28`), 상태색은 별도 값으로 분리된다. `CHANGELOG.md:45-46`이 약속한 "주황색 버튼"은 그대로 유지된다. 새 토큰을 만들지 않고, 저장소에 이미 존재하는 상태색을 쓴다.

`#c26c00`은 `#ff9500`보다 어두워 흰 글자(`style.qss:27` `color:#ffffff`)와의 대비도 함께 개선되지만, 그것은 이 결정의 부수 효과이고 근거가 아니다.

## Reuse

- `docs/ui-mockup-v12.html:64-65` — `.btn.warn{background:#c26c00}` / `:hover{background:#d47700}`
- `style.qss:26-27`의 기본 버튼 형태(`border-radius`, `padding`, `font-weight`, `color:#ffffff`)를 그대로 상속한다 — dirty 규칙은 배경색만 덮어쓰는 기존 구조를 유지한다
- Exemplar: `src/etreport/ui/style.qss:28` (`QPushButton:hover`가 배경색 하나만 덮어쓰는 패턴)

새 프리미티브는 필요 없다.

## Changes

1. `src/etreport/ui/style.qss`
   - Change: 30-31행을 아래로 교체한다.
     ```
     QPushButton[dirty="true"] { background:#c26c00; }
     QPushButton[dirty="true"]:hover { background:#d47700; }
     ```
   - Preserve: 선택자(`QPushButton[dirty="true"]`)와 **선언 위치·순서를 그대로 둔다** — 29행 `:disabled`와 32행 `[ghost="true"]` 사이에 남아야 한다. 배경색 외의 속성을 추가하지 않는다(글자색·라운드·패딩은 26-27행에서 상속).
   - Verify: `myenv/bin/python app.py --demo` → [분석] 워크스페이스에서 그룹 색이나 조건을 바꿔 dirty 상태를 만들면 [그리기]가 어두운 주황(`#c26c00`)이 되고, 그래프의 1번 그룹 색(`#ff9500`)과 눈으로 구분된다.

`group_dialog.py:28`의 팔레트는 **수정하지 않는다** — 계열색으로서의 `#ff9500`은 올바른 사용이다.

## Scope

- Inherit: `analysis_ws.py`의 `btn_apply`(`:325`), `btn_draw`(`:585`, `:967`), `btn_build`(`:731`) — dirty 상태가 되는 순간 새 색을 받는다
- Verify: 세 버튼의 dirty **해제** 경로(`:351`, `:642`, `:1124`, `:833`)에서 기본 파란색(`#0071e3`)으로 정상 복귀하는지. 그리고 `group_dialog.py`의 그룹 색 선택 UI에서 `#ff9500` 스와치가 그대로 남아 있는지.
- Exclude:
  - `group_dialog.py:28`의 계열 팔레트 6색 — 목업 `:2279`와 일치하는 올바른 상태다
  - 목업이 가진 다른 dirty 표식(`:112` 프리셋 점, `:189` 탭 점, `:232` 설정 점) — 코드에 구현돼 있지 않고, `CLAUDE.md:67-70`이 수용한 동작은 "버튼이 주황색"뿐이다. 새 표식 추가는 이 플랜의 범위가 아니다.
  - `#a85e00`(`style.qss:20-21`의 경고 텍스트·박스 색) — 경고 메시지용이며 버튼 상태색과 역할이 다르다. 건드리지 않는다.

## Validation

- Product: [분석]에서 조건을 바꾼 뒤 [그리기]를 누르지 않은 상태 — 버튼이 "지금 눌러야 한다"를 알리되, 그 색이 그래프 안의 어떤 그룹 색과도 겹치지 않아야 한다.
- Interface:
  - 상태: dirty / dirty+hover / 기본 / 기본+hover / 비활성 — 다섯 상태가 구분되는지
  - 화면: 탐색 탭 [그리기], Summary 탭 [표 만들기], 리포트 탭 [미리보기], 좌측 도크 [적용]
  - 동시 표시: 그래프에 `#ff9500` 그룹이 그려진 상태에서 [그리기]가 dirty가 되는 조합을 반드시 확인한다 — 이 플랜이 해결하려는 바로 그 상황이다
- System: `grep -rn 'ff9500' src/` 결과가 `group_dialog.py:28` **한 건만** 남아야 한다. `style.qss`에서 사라졌는지가 이 변경의 핵심 확인점이다.
- Repository: `grep -n 'dirty' src/etreport/ui/style.qss` → 2줄이 각각 `#c26c00`, `#d47700`을 갖는다.

## Stop conditions

- `#ff9500`이 dirty 색으로 의도적으로 선택되었다는 근거(주석·기록·사용자 확인)가 나오면 중단한다. 이 플랜의 전제는 "계열색이 상태색으로 새어 들어갔다"이다.
- `#c26c00`이 실제 화면에서 `style.qss:20-21`의 경고색 `#a85e00`과 혼동될 만큼 가까워 보이면 중단하고, 상태색 토큰 체계를 함께 정하는 더 넓은 범위로 다시 잡는다.
- dirty 색을 목업에 없는 제3의 값으로 정해야 한다는 요구가 생기면 중단한다 — 새 토큰을 만드는 결정이며 별도 범위다.

## Design documentation

- 수용·검증 후: `src/etreport/ui/style.qss:30` 위에 한 줄 주석을 남긴다 — `/* 상태색. #ff9500은 계열색(group_dialog)이므로 쓰지 않는다 */`. 이 이중 사용은 다시 발생하기 쉬운 종류이고, `style.qss`는 주석으로 의도를 남기는 관행이 있다(`style.qss:1`).
- 목업(`docs/ui-mockup-v12.html`)은 수정하지 않는다. 목업이 코드보다 뒤처져 있다는 사실 자체의 정리는 이 플랜의 범위가 아니다.

## 관련 플랜

- `ghost-button-disabled-state.md` — 같은 파일의 인접 규칙(`style.qss:29`, `:32-35`)을 다룬다. 두 플랜을 함께 적용할 경우, 이 플랜은 30-31행을 **제자리에서 교체**하고 다른 플랜은 35행 뒤에 **추가**하므로 충돌하지 않는다.
