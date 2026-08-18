"""**범주 축** — boxplot의 x축이 될 수 있는 것들을 한 곳에서 정한다.

산점도의 x는 늘 숫자(item)였지만 boxplot의 x는 "무엇으로 나눌 것인가"다. 나눌
근거가 여러 군데에서 온다 — DB의 측정 조건(step·온도·site), lot·wafer, 실험 조건
그룹, 그리고 fab tracking에서 뽑아 붙인 이름 붙인 컬럼. 이걸 화면·PPT·표가
각자 해석하면 같은 그림이 세 가지로 갈린다. 그래서 **후보 목록과 값 만드는 법을
여기 하나에** 둔다(`render/ranges.py`가 축 규칙을 독점하는 것과 같은 이유).

`LOT_WAFER`만 데이터에 없는 **가상 컬럼**이다 — lot과 wafer를 합친 이름은
사용자가 늘 원하지만 DB에 그런 컬럼이 있을 리 없고, 적재할 때 만들어 두면
lot·wafer 표기 규칙(`model/wafers`)이 두 곳이 된다. 그릴 때 만든다.
"""
from __future__ import annotations

import polars as pl

#: lot과 wafer를 합친 가상 범주. 이름에 `+`가 들어가 실제 컬럼과 겹치지 않는다.
LOT_WAFER = "lot+wafer"
#: 실험 조건 그룹(gid) — 값은 그룹 **이름**으로 바꿔서 보여 준다.
GROUP = "gid"
#: lot·wafer를 합칠 때 쓰는 구분자. 표기 자체가 계약이라 여기서만 정한다.
SEP = "·"

#: 항상 후보로 두는 것 — 프레임에 있으면 보인다. 앞쪽이 위에 뜬다.
BUILTIN: tuple[str, ...] = (LOT_WAFER, "lot", "wafer", GROUP,
                            "step", "temp", "site")

#: 값이 이만큼을 넘으면 boxplot이 읽을 수 없게 된다 — 경고에 쓴다.
MANY_CATEGORIES = 40


def choices(df: pl.DataFrame | None, extra: list[str] | None = None
            ) -> list[str]:
    """이 프레임에서 x축으로 쓸 수 있는 범주 이름들.

    `extra`는 fab tracking에서 뽑아 붙인 컬럼처럼 **이름을 사용자가 정한** 것들이다
    (`state.track_columns`). 코드가 추측할 수 없으므로 받아서 합친다.

    숫자 컬럼(=ET item·계측값)은 후보에 넣지 않는다 — 값마다 상자가 하나씩 생겨
    boxplot이 무의미해진다. 나누고 싶으면 그 값을 tracking 컬럼으로 뽑아 오는 게
    맞는 흐름이다.
    """
    if df is None:
        return [LOT_WAFER]
    out = [c for c in BUILTIN if c == LOT_WAFER or c in df.columns]
    for name in (extra or []):
        if name in df.columns and name not in out:
            out.append(name)
    # 남은 문자열 컬럼도 후보로 — 손으로 만든 DB에 area·recipe가 들어 있을 수 있다
    for c in df.columns:
        if c in out or c in ("key",):
            continue
        if df.schema[c] == pl.Utf8:
            out.append(c)
    return out


def is_category(name: str, df: pl.DataFrame | None) -> bool:
    """이 이름을 boxplot x축으로 쓸 수 있는가.

    **컬럼이 있다는 것만으로는 부족하다** — ET item도 컬럼이지만 숫자라서 값마다
    상자가 하나씩 생긴다. `choices()`와 같은 기준(숫자는 범주가 아니다)을 쓴다.
    두 함수가 갈리면 "고른 값이 목록에 있는데 안 먹는다"가 된다.
    """
    if not name:
        return False
    if name == LOT_WAFER:
        return True
    if df is None or name not in df.columns:
        return False
    return not df.schema[name].is_numeric()


def series(df: pl.DataFrame, name: str,
           labels: dict[str, str] | None = None) -> pl.Series:
    """범주 이름 하나 → 그 프레임의 **문자열** 값 시리즈.

    `labels`는 gid → 보여 줄 이름(그룹 이름)이다. gid는 `x0`·`x1` 같은 내부
    식별자라 그대로 축에 적으면 아무 뜻이 없다. 매핑이 없으면 원래 값을 쓴다.
    NULL은 `(없음)`으로 — 축에서 빠져 조용히 사라지면 wafer 수가 맞지 않는다.
    """
    if name == LOT_WAFER:
        vals = (pl.concat_str([pl.col("lot").cast(pl.Utf8).fill_null("?"),
                               pl.col("wafer").cast(pl.Utf8).fill_null("?")],
                              separator=SEP))
    elif name in df.columns:
        vals = pl.col(name).cast(pl.Utf8)
    else:
        return pl.Series(name, ["(없음)"] * df.height, dtype=pl.Utf8)
    out = df.select(vals.fill_null("(없음)").alias(name))[name]
    if labels and name == GROUP:
        out = out.replace_strict(labels, default=None).fill_null(out)
    return out


def group_labels(styles) -> dict[str, str]:
    """GroupStyle 목록 → {gid: 표시 이름}. 미배정('')은 그대로 둔다."""
    return {g.gid: g.name for g in (styles or []) if g.gid}


def order(values: list[str]) -> list[str]:
    """상자를 놓을 순서 — 숫자로 읽히면 숫자 순, 아니면 사전 순.

    온도(`-40 25 125`)나 site 수처럼 숫자가 문자열로 들어오는 범주가 많다.
    사전 순으로만 늘어놓으면 `-40 125 25`가 되어 축을 읽을 수 없다.
    """
    def key(v: str) -> tuple[int, float, str]:
        try:
            return (0, float(v), "")
        except (TypeError, ValueError):
            return (1, 0.0, v)
    return sorted(set(values), key=key)
