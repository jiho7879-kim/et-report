"""입력 가이드의 **단일 진실** — 무엇이 필요한지 여기서만 판정한다(설계 §5).

세 곳이 같은 함수를 읽는다:

  · **필요 표시**(층 1) — 비어 있는 필수 입력에 `needs="true"`를 달아 QSS가
    왼쪽 액센트 바로 그린다. 채우면 조용히 사라진다.
  · **가이드 모드**(층 2) — `F2`로 켜면 **다음에 할 한 곳만** 강조하고 한 줄
    설명을 붙인다. 다 채우면 저절로 꺼진다.
  · **빈 상태 화면** — 아무것도 안 물린 캔버스·표·슬라이드가 "지금 무엇이 없고
    무엇을 누르면 되는지" 말한다.

셋이 각자 판정하면 화면마다 다른 말을 한다. 더 중요한 것은 **`[적용]`의 실제
검증과 어긋나면 안 된다**는 점이다 — "표시는 초록인데 적용은 실패"가 가장
나쁜 결과다. 그래서 판정 기준은 `model/session.apply_config`가 실제로 요구하는
것을 그대로 따른다:

  · 리포메터가 없으면 ALIAS가 없어 템플릿의 x·y가 아무것도 가리키지 못한다.
  · 템플릿은 **plot과 table이 둘 다 있어야** 읽는다(`apply_config` 2단계의
    `if cfg.plot_template_path and cfg.table_template_path`). 그래서 Table은
    "있으면 좋은 것"이 아니라 필수다 — 예전 화면은 이것을 말하지 않았다.
  · 실험 조건은 없어도 된다(그룹은 손으로 짤 수 있다).
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Requirement", "analysis_requirements", "data_requirements",
           "empty_message", "next_step"]


@dataclass(frozen=True)
class Requirement:
    """채워야 하는 것 하나.

    key      위젯을 찾는 이름(파일 행 키·버튼 이름). 화면이 이걸로 강조한다.
    label    사람에게 보일 이름
    done     이미 채워졌나
    how      다음에 할 일 **한 줄**
    optional 없어도 [적용]이 되는가
    """

    key: str
    label: str
    done: bool
    how: str
    optional: bool = False


def analysis_requirements(state, cfg) -> list[Requirement]:
    """분석 화면 순서 — ① DB → ② Plot/Table 템플릿 → ③ 리포메터 → ④ [적용] → ⑤ 그룹."""
    split = bool(getattr(cfg, "split_path", "")
                 or getattr(cfg, "split_text", ""))
    return [
        Requirement("db", "DB", bool(cfg.db_path),
                    "왼쪽 [DB]를 눌러 분석할 .duckdb 파일을 고르세요"),
        Requirement("plot", "Plot 템플릿", bool(cfg.plot_template_path),
                    "[Plot]에서 plot 템플릿(엑셀·csv)을 고르세요"),
        Requirement("tbl", "Table 템플릿", bool(cfg.table_template_path),
                    "[Table]도 함께 고르세요 — 템플릿은 plot·table이 짝일 때 읽습니다"),
        Requirement("rfm", "리포메터", bool(cfg.reformatter_path),
                    "[리포메터]를 고르세요 — 템플릿의 x·y가 가리키는 ALIAS입니다"),
        Requirement("split", "실험 조건", split,
                    "실험(split) 비교를 하려면 [실험 조건]을 고르세요 — 없어도 됩니다",
                    optional=True),
        Requirement("apply", "적용", bool(state.applied and state.data is not None),
                    "[적용](F5)을 눌러 고른 파일을 한 번에 읽고 검증하세요"),
        Requirement("group", "그룹", bool(state.groups),
                    "그룹이 없으면 전체가 한 덩이로 그려집니다 — "
                    "인스펙터 [그룹]에서 만들 수 있습니다",
                    optional=True),
    ]


def data_requirements(preset, line_id: str = "",
                      days: int = 0) -> list[Requirement]:
    """데이터 화면 순서 — ① 대상 DB → ② 리포메터 → ③ line_id → ④ 기간.

    `line_id`와 `days`는 프리셋이 아니라 **화면의 입력**에서 온다(조건 행과
    날짜 칸). 프리셋에는 그 값이 없다 — 조건은 목록이고 기간은 날짜 두 개다.
    """
    return [
        Requirement("db", "대상 DB", bool(getattr(preset, "db_path", "")),
                    "적재할 .duckdb 파일 경로를 정하세요 (없으면 새로 만듭니다)"),
        Requirement("rfm", "리포메터", bool(getattr(preset, "reformatter_path", "")),
                    "리포메터를 고르세요 — 어떤 item을 뽑을지가 여기서 정해집니다"),
        Requirement("line", "line_id", bool(line_id),
                    "line_id를 적으세요 — 조회 범위를 좁히는 첫 조건입니다"),
        Requirement("period", "기간", int(days) > 0,
                    "며칠치를 뽑을지 정하세요"),
    ]


def next_step(reqs: list[Requirement]) -> Requirement | None:
    """아직 안 채운 **필수** 항목 중 첫 번째. 다 됐으면 None."""
    return next((r for r in reqs if not r.done and not r.optional), None)


def empty_message(state, what: str) -> str:
    """빈 화면 문구 — `what`은 `plot` · `table` · `report`.

    빈 상태와 가이드가 다른 말을 하면 안 되므로 문장을 여기서 만든다.
    """
    if state.data is None:
        return ("불러온 데이터가 없습니다.\n"
                "왼쪽에서 DB를 고르고 [적용](F5)을 누르세요.\n"
                "아직 적재한 DB가 없으면 [데이터] 화면(Ctrl+1)에서 추출하세요.")
    if what == "table":
        if state.report is None:
            return ("Table 템플릿이 아직 없습니다.\n"
                    "왼쪽에서 Plot·Table 템플릿을 함께 고른 뒤 [적용](F5)을 "
                    "누르면 CAT1마다 표가 한 장씩 만들어집니다.")
        return "아래 [표 만들기](Ctrl+Enter)를 누르면 표가 만들어집니다."
    if what == "report":
        if state.report is None:
            return ("Plot 템플릿이 아직 없습니다.\n"
                    "왼쪽에서 Plot·Table 템플릿을 고르고 [적용](F5)을 누르세요.")
        return "아래 [미리보기](Ctrl+Enter)를 누르면 이 페이지를 그립니다."
    if not state.rf.rules:
        return ("리포메터가 없어 item 이름을 알 수 없습니다.\n"
                "왼쪽에서 [리포메터]를 고르고 [적용](F5)을 누르세요.")
    return "오른쪽 인스펙터에서 X·Y를 고르고 아래 [그리기](Ctrl+Enter)를 누르세요."
