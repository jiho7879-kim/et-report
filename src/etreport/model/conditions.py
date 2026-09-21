"""측정 조건 필터 — step · site · temp 로 **분석에 쓸 데이터 자체**를 좁힌다.

그룹 편집 창의 4단 필터(§9.1)와 이름이 같지만 하는 일이 다르다. 그쪽은
*배정 범위*(같은 wafer라도 step·온도가 다르면 다른 측정점)를 정하고, 이쪽은
*요약 표·plot·PPT가 무엇으로 만들어지는가*를 정한다. 예전에는 후자가 없어서
조건을 좁혀 보려면 그룹 편집 창을 열어야 하는 것처럼 보였는데, 그 창은 요약
표 화면에서는 아예 보이지 않는다 — 결과가 만들어지기 전에 정해야 하는 값이
결과를 보는 자리에서 보이지 않았다.

규칙 하나: **빈 값 = 좁히지 않음**(lot 선택 §9.2와 같은 관용구). 고르지 않은
상태에서는 프레임이 글자 하나까지 예전과 같다.

값은 **문자열로만** 비교한다. temp는 읽는 시점에 5단위로 보정되어(`compat.
temp_expr`) DB마다 int·double이 갈리는데, 양쪽을 같은 규칙으로 Utf8에 태우면
목록에 보이는 값과 걸리는 값이 반드시 일치한다.
"""
from __future__ import annotations

import polars as pl

#: 좁히지 않음. 콤보의 `전체` 항목이 갖는 값이다.
ALL = ""

#: (화면 라벨, 프레임 컬럼) — 순서가 곧 레일에 놓이는 순서다(§9.1과 같은 순서).
FIELDS: tuple[tuple[str, str], ...] = (
    ("step_id", "step"), ("site", "site"), ("temp", "temp"))

NAMES: tuple[str, ...] = tuple(name for _label, name in FIELDS)

#: 제목·툴팁에 쓰는 짧은 이름
SHORT = {"step": "step", "site": "site", "temp": "온도"}


def pretty(value: str) -> str:
    """보여 줄 표기 — `25.0`은 `25`로. **거르는 값은 바뀌지 않는다.**

    온도는 5단위 정수로 보정되어 들어오는데 DB 타입이 double이면 문자열이
    `25.0`이 된다. 화면에만 정리하고, 실제 비교는 프레임과 같은 원래 문자열로
    한다 — 표기를 값으로 쓰면 int 컬럼과 double 컬럼에서 결과가 갈린다.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f.is_integer() else str(value)


def chip(name: str, value: str) -> str:
    """한 조건을 한 조각으로 — `25℃` · `step M2ET` · `site 9`."""
    v = pretty(value)
    return f"{v}℃" if name == "temp" else f"{SHORT.get(name, name)} {v}"


def _sort_key(v: str):
    """숫자로 읽히면 숫자 순, 아니면 글자 순 — 25·85·125가 125·25·85로 서지 않게."""
    try:
        return (0, float(v), "")
    except ValueError:
        return (1, 0.0, v)


def choices(df: pl.DataFrame | None) -> dict[str, list[str]]:
    """프레임에 실제로 들어 있는 조건 값들. **좁히기 전 프레임**으로 만든다.

    좁힌 뒤에 만들면 한 번 고른 값 말고는 목록에서 사라져 되돌릴 수가 없다.
    """
    out: dict[str, list[str]] = {}
    if df is None:
        return out
    for name in NAMES:
        if name not in df.columns:
            continue
        vals = {v for v in df[name].cast(pl.Utf8).to_list() if v is not None}
        if vals:
            out[name] = sorted(vals, key=_sort_key)
    return out


def normalize(conds: dict[str, str] | None) -> dict[str, str]:
    """빈 값(=전체)을 걸러 낸 조건만. 저장·비교·표시가 전부 이 모양을 쓴다."""
    return {k: str(v) for k, v in (conds or {}).items()
            if k in NAMES and str(v or "").strip() != ALL}


def apply(df: pl.DataFrame | None,
          conds: dict[str, str] | None) -> tuple[pl.DataFrame | None, int]:
    """조건으로 좁힌 프레임과 **버린 행 수**. 조건이 없으면 원본 그대로."""
    use = normalize(conds)
    if df is None or not use:
        return df, 0
    before = df.height
    for name, val in use.items():
        if name in df.columns:
            df = df.filter(pl.col(name).cast(pl.Utf8) == val)
    return df, before - df.height


def label(conds: dict[str, str] | None) -> str:
    """레일 제목에 붙일 요약 — 없으면 빈 문자열('전체'라고 적지 않는다)."""
    use = normalize(conds)
    return " · ".join(chip(k, use[k]) for k in NAMES if k in use)
