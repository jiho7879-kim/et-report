"""lot·wafer 표기 정규화 — **매칭은 여기 한 곳에서만 판정한다**.

현장에서 wafer는 `W01` · `W1` · `01` · `1` 네 가지로 섞여 들어온다.
ET 계측 DB는 `W01`, fab tracking은 `01`, 사람이 손으로 적은 실험 조건표는
`1`인 식이다. 예전에는 붙여넣기 경로마다 제각각(`W{int(wf):02d}`) 맞춰 보다가
하나라도 표기가 다르면 배정이 조용히 비어 버렸다.

규칙은 하나다 — **비교는 정규화한 키로, 저장은 DB에 실제로 있는 표기로.**
  `norm_wafer()` : 표기를 지우고 비교용 키를 만든다 (W01·W1·01·1 → "1")
  `resolve()`    : 정규화 키로 실제 값 목록에서 원래 표기를 찾아 준다

lot도 같은 이유로 `norm_lot()`(공백 제거·대문자)를 쓴다. 표기가 달라
매칭이 깨지는 자리(실험 조건 배정 · 그룹 붙여넣기 · 계측 조인)는 전부
이 모듈을 경유한다.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

import polars as pl

#: 앞의 W(afer)/S(lot) 표식과 구분자를 떼어 낸다 — "W-01", "wf01", "#1"도 포함
_LEAD = re.compile(r"^[\s#]*(?:WAFER|WF|SLOT|W|S)?[\s._\-#]*", re.IGNORECASE)


def norm_lot(v: object) -> str:
    """lot 비교 키 — 공백을 없애고 대문자로. None은 빈 문자열."""
    if v is None:
        return ""
    return str(v).strip().upper()


def norm_wafer(v: object) -> str:
    """wafer 비교 키 — `W01` `W1` `01` `1` 이 모두 `"1"`이 된다.

    숫자로 떨어지지 않는 표기(`EDGE`, `DUMMY` 등)는 대문자로만 정리해 그대로
    쓴다 — 모르는 표기를 숫자로 억지로 바꾸면 서로 다른 wafer가 합쳐진다.
    """
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    body = _LEAD.sub("", s, count=1)
    if body.isdigit():
        return str(int(body))              # 앞의 0을 떼어 "01" == "1"
    # "W01.2" 처럼 뒤에 뭐가 붙은 경우: 숫자 부분만이라도 맞춰 본다
    m = re.fullmatch(r"(\d+)([A-Za-z].*)?", body)
    if m:
        return str(int(m[1])) + (m[2].upper() if m[2] else "")
    return s.upper()


def key(lot: object, wafer: object) -> tuple[str, str]:
    """(lot, wafer) 비교 키 한 쌍."""
    return norm_lot(lot), norm_wafer(wafer)


def map_gids(lots: Iterable[object], wafs: Iterable[object],
             assign: dict[tuple[str, str], str]) -> pl.Series:
    """(lot, wafer) 열 → gid 열. `assign`은 `key()`로 만든 키를 쓴다.

    실험 조건 배정을 프레임에 붙이는 자리가 네 곳(loader·analysis_ws·
    deckbuild·demo)이라 조회 규칙을 여기 한 번만 둔다.

    **dtype을 명시해 Series로 돌려준다.** 리스트로 주면 프레임이 비었을 때
    (lot 선택·측정 조건으로 0행이 되는 흔한 경우) polars가 dtype을 Null로
    추론하고, 그 열에 `gid != ""`를 걸면 `series type Null does not have neq
    operator`로 화면이 죽는다(리포트 탭 showEvent에서 발견).
    """
    return pl.Series([assign.get(key(lo, wa), "") for lo, wa in zip(lots, wafs)],
                     dtype=pl.Utf8)


def index(values: Iterable[object]) -> dict[str, str]:
    """실제 값 목록 → {정규화 키: 실제 표기}. 먼저 나온 표기가 이긴다."""
    out: dict[str, str] = {}
    for v in values:
        k = norm_wafer(v)
        if k and k not in out:
            out[k] = str(v)
    return out


def resolve(typed: object, values: Iterable[object]) -> str | None:
    """사용자가 적은 wafer를 실제 값 목록의 표기로 바꿔 준다. 없으면 None."""
    return index(values).get(norm_wafer(typed))


# ── polars 식 (프레임 전체를 한 번에 정규화할 때) ─────────────
def lot_key_expr(col: str = "lot") -> pl.Expr:
    """`norm_lot`의 벡터판 (별칭 없음 — 쓰는 쪽에서 붙인다)."""
    return (pl.col(col).cast(pl.Utf8).str.strip_chars()
            .str.to_uppercase().fill_null(""))


def wafer_key_expr(col: str = "wafer") -> pl.Expr:
    """`norm_wafer`의 벡터판 — 숫자로 떨어지는 표기만 앞의 0을 뗀다.

    행마다 파이썬을 부르지 않도록 문자열 연산으로 쓴다. 숫자가 아닌 표기는
    대문자 정리까지만 하고 그대로 둔다(스칼라판과 같은 결정).
    """
    s = pl.col(col).cast(pl.Utf8).str.strip_chars()
    body = s.str.replace(r"(?i)^[\s#]*(?:WAFER|WF|SLOT|W|S)?[\s._\-#]*", "")
    digits = body.str.extract(r"^(\d+)$", 1)
    return (pl.when(digits.is_not_null())
            .then(digits.cast(pl.Int64, strict=False).cast(pl.Utf8))
            .otherwise(body.str.to_uppercase())
            .fill_null(""))
