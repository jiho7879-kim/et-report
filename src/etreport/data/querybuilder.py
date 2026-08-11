"""조건 토큰 → Impala SQL WHERE 절.

문자열 컬럼 : 띄어쓰기=여러 값, ``!``=제외(NOT IN … OR IS NULL), ``*``=LIKE,
             "…"=공백 포함, 정규식 모드(RE2 · 부분매칭 · lookahead 불가)
숫자 컬럼   : ``>=25`` ``<85`` ``25~85`` ``25 85 125`` ``!0``
TIMESTAMP  : ``2026-08-01 ~ 2026-08-10``  (상한은 exclusive)

주의: NOT IN은 NULL 함정이 있어 항상 ``OR col IS NULL``을 붙인다.
"""
from __future__ import annotations

import re
import shlex
from datetime import date, timedelta

from etreport.config.catalog import Catalog
from etreport.config.settings import Condition

_CMP = re.compile(r"^(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)$")
_RANGE = re.compile(r"^(-?\d+(?:\.\d+)?)~(-?\d+(?:\.\d+)?)$")
_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")


def _q(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


class ConditionError(ValueError):
    """UI가 행 단위로 표시할 수 있게 컬럼명을 담는다."""

    def __init__(self, col: str, msg: str) -> None:
        super().__init__(msg)
        self.col = col


def _string_sql(col: str, val: str, mode: str) -> str:
    if mode == "regexp":
        return f"{col} REGEXP {_q(val)}"
    inc: list[str] = []
    exc: list[str] = []
    like: list[str] = []
    for tok in shlex.split(val):          # "…" 공백 포함 지원
        if tok.startswith("!"):
            exc.append(tok[1:])
        elif "*" in tok:
            like.append(tok.replace("%", r"\%").replace("*", "%"))
        else:
            inc.append(tok)
    parts: list[str] = []
    pos: list[str] = []
    if like:
        pos += [f"{col} LIKE {_q(p)}" for p in like]
    if inc:
        pos.append(
            f"{col} IN ({', '.join(_q(x) for x in inc)})" if len(inc) > 1
            else f"{col} = {_q(inc[0])}"
        )
    if pos:
        parts.append(f"({' OR '.join(pos)})" if len(pos) > 1 else pos[0])
    if exc:
        parts.append(
            f"({col} NOT IN ({', '.join(_q(x) for x in exc)}) OR {col} IS NULL)"
        )
    if not parts:
        raise ConditionError(col, "값이 비어 있습니다")
    return " AND ".join(parts)


def _numeric_sql(col: str, val: str) -> str:
    eq: list[str] = []
    parts: list[str] = []
    for tok in val.split():
        if m := _CMP.match(tok):
            parts.append(f"{col} {m[1]} {m[2]}")
        elif m := _RANGE.match(tok):
            parts.append(f"{col} BETWEEN {m[1]} AND {m[2]}")
        elif tok.startswith("!") and _NUM.match(tok[1:]):
            parts.append(f"({col} <> {tok[1:]} OR {col} IS NULL)")
        elif _NUM.match(tok):
            eq.append(tok)
        else:
            raise ConditionError(col, f"숫자가 아닙니다: {tok}")
    if eq:
        parts.insert(0, f"{col} IN ({', '.join(eq)})" if len(eq) > 1 else f"{col} = {eq[0]}")
    if not parts:
        raise ConditionError(col, "값이 비어 있습니다")
    return f"({' AND '.join(parts)})" if len(parts) > 1 else parts[0]


def condition_sql(cond: Condition, catalog: Catalog) -> str:
    info = catalog.get(cond.col)
    val = cond.val.strip()
    if not val:
        raise ConditionError(cond.col, "값이 비어 있습니다")
    if info and info.is_numeric:
        return _numeric_sql(cond.col, val)
    if info and info.is_timestamp:
        # 문자열 리터럴로 비교 — Impala가 TIMESTAMP로 암시적 캐스팅한다
        lo, _, hi = (x.strip() for x in val.partition("~"))
        if hi:
            return f"{cond.col} >= {_q(lo)} AND {cond.col} < {_q(hi)}"
        return f"{cond.col} >= {_q(lo)}"
    return _string_sql(cond.col, val, cond.mode)


# SELECT 절에 들어갈 컬럼. 리스트로 두고 쓸 때 쉼표로 잇는다
# (튜플을 그대로 f-string에 넣으면 괄호가 SQL에 섞여 들어간다).
KEY_COLS: list[str] = [
    "line_id",
    "root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos", "temperature",
    "step_id", "step_seq", "total_site_cnt", "tkout_time",
    "item_id", "et_value",
]

SELECT_COLS = ", ".join(KEY_COLS)


def build_extract_sql(
    conditions: list[Condition],
    d_from: date,
    d_to: date,                 # inclusive — SQL에서는 +1일 미만
    catalog: Catalog,
    table: str = "eds.f_et_test",
) -> str:
    line = next((c for c in conditions if c.required), None)
    if line is None or not line.val.strip():
        raise ConditionError("line_id", "line_id는 필수입니다 (파티션 프루닝)")
    hi = d_to + timedelta(days=1)
    # tkout_time은 반드시 문자열 리터럴로 넘긴다 (Impala가 TIMESTAMP로 캐스팅)
    where = [
        condition_sql(line, catalog),
        f"tkout_time >= '{d_from:%Y-%m-%d} 00:00:00'",
        f"tkout_time <  '{hi:%Y-%m-%d} 00:00:00'",
    ]
    where += [
        condition_sql(c, catalog)
        for c in conditions
        if not c.required and c.val.strip()
    ]
    body = "\n  AND  ".join(where)
    return (
        f"SELECT {SELECT_COLS}\n"
        f"FROM   {table}\n"
        f"WHERE  {body}"
    )
