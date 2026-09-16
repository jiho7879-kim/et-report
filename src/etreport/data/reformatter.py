"""리포메터 — CATEGORY·ITEMID·ALIAS·ABSOLUTE·SCALE FACTOR·ADDP FORM·
UNIT·SPECLOW·SPECHIGH·TARGET.

처리 순서(확정 사양):
  1) REAL item에 SCALE FACTOR 적용  (수식 계산 **전**)
  2) ABSOLUTE == 'Y' 이면 절대값
  3) ADDP를 시트의 **행 순서대로** 계산 — 아래 행은 위 행의 ADDP를 참조할 수
     있고 그 반대는 검증 오류. 순서 규칙이 곧 순환참조 차단이다.

수식 문법: ``{ALIAS}`` 참조, 사칙연산, 그리고 화이트리스트 함수만.
eval()은 쓰지 않는다 — ast로 파싱해 직접 걷는다.
``Std({A},{B},...)`` 는 그룹 표본표준편차(n-1, 엑셀 STDEV와 같은 값)다:
(root_lot_id, wafer_id, step_id, step_seq, total_site_cnt, temperature) 6키로
묶어 인자들을
그룹 안의 포인트들로 보고 한 번에 계산하고, 그룹의 모든 chip에 같은 값을
채운다(broadcast). NULL 인자는 빼고, 유효값이 2개 미만이면 NULL.
6키 컬럼이 데이터에 없으면 예전처럼 행 단위(수평) 산포로 계산한다.
키 값이 NULL이어도 NULL끼리 한 묶음으로 계산한다.
"""
from __future__ import annotations

import ast
import logging
import math
import re
from dataclasses import dataclass, field

import polars as pl

COLUMNS = [
    "CATEGORY", "ITEMID", "ALIAS", "ABSOLUTE", "SCALE FACTOR",
    "ADDP FORM", "UNIT", "SPECLOW", "SPECHIGH", "TARGET",
]

log = logging.getLogger(__name__)

_REF = re.compile(r"\{([^{}]+)\}")

# Std() 그룹 표본표준편차의 묶음 키 (요청 ⑤ 확정 사양).
STD_KEYS = ("root_lot_id", "wafer_id", "step_id", "step_seq",
            "total_site_cnt", "temperature")

# Std 호출의 머리 — 인자는 괄호 짝을 따라가며 _std_calls()가 자른다.
# Std(Abs({A}),{B})처럼 인자에 식이 들어가도 정규식 하나로는 끝을 못 찾는다.
_STD_HEAD = re.compile(r"(?<![A-Za-z0-9_])Std(?:dev|ev)?\s*\(", re.IGNORECASE)


def _norm_header(name: str) -> str:
    """헤더 정규화 키 — 대소문자·공백·밑줄을 무시한다.
    'ADDP FORM' == 'ADDPFORM' == 'ADDP_FORM' == 'addp form'."""
    return re.sub(r"[\s_]+", "", str(name)).lower()


# 정규화 키 → 표준 헤더. load()에서 유저가 헤더를 조금씩 다르게 적은 것을
# 같은 컬럼으로 받아들이는 데 쓴다.
_CANON_BY_NORM = {_norm_header(c): c for c in COLUMNS}


# ABSOLUTE 같은 참/거짓 셀 — 확정 사양(§3.1). 대소문자 무관, 빈칸은 거짓.
_TRUE_TOKENS = {"TRUE", "T", "Y", "1", "O"}
_FALSE_TOKENS = {"FALSE", "F", "N", "0", "X", ""}


def parse_flag(v) -> bool | None:
    """엑셀 셀 → 참/거짓. **모르는 값이면 None**을 돌려 호출부가 경고하게 한다.

    엑셀에서 오는 모양이 제각각이다 — 체크박스는 파이썬 bool, 숫자 셀은 1.0/0.0,
    나머지는 문자열(TRUE/Y/O/X…). 예전에는 'Y' 하나만 참으로 봐서 리포메터에
    `TRUE`나 `1`로 적힌 행의 절대값이 조용히 무시됐다.
    """
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v) if float(v) in (0.0, 1.0) else None
    s = str(v).strip().upper()
    if s in _TRUE_TOKENS:
        return True
    if s in _FALSE_TOKENS:
        return False
    try:                       # 엑셀 숫자가 '1.0' 문자열로 온 경우
        f = float(s)
    except ValueError:
        return None
    return bool(f) if f in (0.0, 1.0) else None


def _num(v) -> float | None:
    """엑셀 셀 → 숫자. None·빈칸은 None, '0.34' 같은 문자열도 허용."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Rule:
    category: str            # REAL | ADDP
    itemid: str
    alias: str
    absolute: bool
    scale: float
    formula: str             # ADDP FORM (REAL이면 빈 문자열)
    unit: str
    speclow: float | None
    spechigh: float | None
    target: float | None
    row: int                 # 시트 행 번호(오류 표시·계산 순서)
    w: float | None = None   # 옵션 기하 컬럼 "WIDTH" (폭)
    l: float | None = None   # 옵션 기하 컬럼 "LENGTH" (길이)  # noqa: E741


@dataclass
class ReformatterError:
    row: int
    alias: str
    message: str


@dataclass
class Reformatter:
    rules: list[Rule] = field(default_factory=list)
    errors: list[ReformatterError] = field(default_factory=list)     # 치명적
    warnings: list[ReformatterError] = field(default_factory=list)   # 건너뛴 행

    def report_lines(self) -> list[str]:
        return [f"{e.row}행 {e.alias}: {e.message}"
                for e in (*self.errors, *self.warnings)]

    @property
    def by_alias(self) -> dict[str, Rule]:
        return {r.alias: r for r in self.rules}

    def reals(self) -> list[Rule]:
        return [r for r in self.rules if r.category == "REAL"]

    def addps(self) -> list[Rule]:
        return [r for r in self.rules if r.category == "ADDP"]


# ── 로딩 ──────────────────────────────────────────────────────
def load(path: str, sheet: str | int = 0) -> Reformatter:
    """xlwings로 리포메터를 읽는다 (배포 PC에 Excel 존재 확인됨).

    sheet: 시트 이름 또는 인덱스 — 한 파일에 여러 리포메터 시트를 두고
    골라 쓸 수 있다. 읽은 직후 검증까지 수행하고 이후 계산은 이 객체만
    쓴다(엑셀 파일은 다시 열지 않는다 — 캐시 역할).
    """
    from etreport.data.xlio import read_sheet

    return from_frame(read_sheet(path, sheet))


def from_frame(raw: pl.DataFrame) -> Reformatter:
    """리포메터 시트 표 하나 → **검증까지 마친** Reformatter.

    파일을 거치지 않는 입구다(데모·테스트). 헤더 정규화·플래그 해석·검증이
    load()와 같은 코드라서 "파일로 읽었을 때만 걸리는 오류"가 생기지 않는다.
    """
    rf = Reformatter()

    # 유저가 헤더를 'ADDP FORM'/'ADDPFORM'/'ADDP_FORM' 등으로 조금씩 다르게
    # 적더라도 같은 컬럼으로 본다(공백·밑줄·대소문자 무시). 정확한 표준 헤더가
    # 있으면 그걸 우선하고, 없을 때만 정규화가 일치하는 헤더를 표준명으로 받는다.
    raw_norm = {_norm_header(c): c for c in raw.columns}
    rename: dict[str, str] = {}
    for canon in COLUMNS:
        if canon in raw.columns:
            continue
        src = raw_norm.get(_norm_header(canon))
        if src is not None:
            rename[src] = canon
    if rename:
        raw = raw.rename(rename)

    missing = [c for c in COLUMNS if c not in raw.columns]
    if missing:
        rf.errors.append(ReformatterError(0, "", f"컬럼 누락: {', '.join(missing)}"))
        return rf

    for i, row in enumerate(raw.iter_rows(named=True), start=2):
        alias = str(row["ALIAS"] or "").strip()
        if not alias:
            continue
        absolute = parse_flag(row["ABSOLUTE"])
        if absolute is None:      # 알 수 없는 값 — 거짓으로 보되 반드시 알린다
            rf.warnings.append(ReformatterError(
                i, alias,
                f"ABSOLUTE '{row['ABSOLUTE']}'를 알 수 없어 거짓으로 봤습니다 "
                f"(참: TRUE/T/Y/1/O · 거짓: FALSE/N/0/X/빈칸)"))
            absolute = False
        rf.rules.append(Rule(
            category=str(row["CATEGORY"] or "REAL").strip().upper(),
            itemid=str(row["ITEMID"] or "").strip(),
            alias=alias,
            absolute=absolute,
            scale=_num(row["SCALE FACTOR"]) or 1.0,
            formula=str(row["ADDP FORM"] or "").strip(),
            unit=str(row["UNIT"] or "").strip(),
            speclow=_num(row["SPECLOW"]),
            spechigh=_num(row["SPECHIGH"]),
            target=_num(row["TARGET"]),
            row=i,
            # WIDTH/LENGTH는 옵션 기하 컬럼 — 시트에 있을 때만 읽는다.
            w=_num(row.get("WIDTH")) if "WIDTH" in raw.columns else None,
            l=_num(row.get("LENGTH")) if "LENGTH" in raw.columns else None,
        ))
    validate(rf)
    return rf


# ── 검증 ──────────────────────────────────────────────────────
def validate(rf: Reformatter) -> None:
    """문제 행을 **버리고** 나머지로 진행한다 (사용자 요청 사양).

    - ALIAS 중복 → 마지막 행만 남긴다 (엑셀에서 아래에 덮어쓰는 습관과 일치)
    - ADDP가 미정의·아래 행 참조·수식 오류 → 그 행을 뺀다
    - 빠진 ADDP를 참조하던 아래 ADDP도 연쇄적으로 뺀다
    빠진 내용은 warnings에 남아 UI가 한 번에 보여준다.
    """
    # 1) ALIAS 중복 — 마지막 승자
    last: dict[str, int] = {}
    for i, r in enumerate(rf.rules):
        last[r.alias] = i
    kept: list[Rule] = []
    for i, r in enumerate(rf.rules):
        if last[r.alias] != i:
            rf.warnings.append(ReformatterError(
                r.row, r.alias,
                f"ALIAS 중복 — 마지막 행({rf.rules[last[r.alias]].row}행)을 사용합니다"))
            continue
        kept.append(r)

    # 2) ADDP 해석 — 위에서 아래로, 실패한 행은 제외
    defined: set[str] = set()
    final: list[Rule] = []
    for r in kept:
        if r.category != "ADDP":
            defined.add(r.alias)
            final.append(r)
            continue
        if not r.formula:
            rf.warnings.append(ReformatterError(
                r.row, r.alias, "ADDP FORM이 비어 있어 제외했습니다"))
            continue
        missing = [ref for ref in _REF.findall(r.formula) if ref not in defined]
        if missing:
            rf.warnings.append(ReformatterError(
                r.row, r.alias,
                f"참조 불가 {', '.join(missing)} — 미정의이거나 아래 행에 있어"
                " 이 item을 제외했습니다"))
            continue
        try:
            compile_formula(r.formula)
        except FormulaError as e:
            rf.warnings.append(ReformatterError(
                r.row, r.alias, f"{e} — 이 item을 제외했습니다"))
            continue
        defined.add(r.alias)
        final.append(r)

    rf.rules = final


# ── 수식 엔진 ─────────────────────────────────────────────────
class FormulaError(ValueError):
    pass


def _std_sample(*vals: float | None) -> float | None:
    """엑셀 STDEV 동치: 표본(n-1), NULL은 빼고 계산. 유효값<2면 NULL."""
    xs = [v for v in vals if v is not None and not math.isnan(v)]
    if len(xs) < 2:
        return None
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def _null_skip(fn):
    def wrap(*vals):
        xs = [v for v in vals if v is not None and not math.isnan(v)]
        return fn(xs) if xs else None
    return wrap


def _abs(v):
    return None if v is None else abs(v)


def _exp(v):
    """지수. 넘치면 inf 대신 NULL — 벡터 경로와 결과를 맞춘다."""
    if v is None:
        return None
    try:
        r = math.exp(v)
    except OverflowError:
        return None
    return r if math.isfinite(r) else None


_BASE_FUNCS = {
    # 파이썬식 이름 → 구현. SQL로 내릴 땐 min→least 등 매핑 주의(계획서 Q8).
    # 함수 목록은 확정 사양(§3.1): ABS SQRT LN LOG LOG10 EXP MIN MAX AVG SUM STD
    # **LN = 자연로그(밑 e), LOG·LOG10 = 상용로그(밑 10)** — 엑셀 관례를 따른다.
    "Std":   _std_sample,
    "Avg":   _null_skip(lambda xs: sum(xs) / len(xs)),
    "Sum":   _null_skip(sum),
    "Min":   _null_skip(min),
    "Max":   _null_skip(max),
    "Abs":   _abs,
    "Sqrt":  lambda v: None if v is None or v < 0 else math.sqrt(v),
    "Log10": lambda v: None if v is None or v <= 0 else math.log10(v),
    "Log":   lambda v: None if v is None or v <= 0 else math.log10(v),
    "Ln":    lambda v: None if v is None or v <= 0 else math.log(v),
    "Exp":   _exp,
}

# 엑셀에서 ABS(), abs() 처럼 아무렇게나 써도 통하도록 대소문자 별칭을 깐다.
_FUNCS: dict = {}
for _n, _f in _BASE_FUNCS.items():
    _FUNCS[_n] = _f
    _FUNCS[_n.upper()] = _f
    _FUNCS[_n.lower()] = _f
# 엑셀 STDEV·SQL STDDEV로 적는 사람이 많다 — 같은 Std로 받는다.
for _n in ("Stdev", "StdDev"):
    for _k in (_n, _n.upper(), _n.lower()):
        _FUNCS[_k] = _std_sample

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd, ast.Load,
)


def compile_formula(src: str):
    """'{A}/{B}' → 호출가능 f(env)  (env: alias→값, 값은 None 가능)."""
    refs = _REF.findall(src)
    py = _REF.sub(lambda m: f"__v{refs.index(m.group(1))}", src)
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"수식 구문 오류: {e.msg}") from e
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(f"허용되지 않는 구문: {type(node).__name__}")
        if isinstance(node, ast.Call) and (
                not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS):
            raise FormulaError(
                f"허용되지 않는 함수: "
                f"{getattr(node.func, 'id', '?')} (가능: {', '.join(_FUNCS)})"
            )
        # {} 없이 쓴 ALIAS는 여기서 걸러야 한다. 그냥 두면 검증을 통과한 뒤
        # 계산 시점에 NameError로 터져서 리포메팅 전체가 중단된다.
        if isinstance(node, ast.Name) and not node.id.startswith("__v") \
                and node.id not in _FUNCS:
            raise FormulaError(
                f"알 수 없는 이름 '{node.id}' — ALIAS는 {{{node.id}}} 처럼 "
                f"중괄호로 감싸야 합니다")
    code = compile(tree, "<addp>", "eval")

    def run(env: dict[str, float | None]):
        loc = {f"__v{i}": env.get(a) for i, a in enumerate(refs)}
        try:
            return eval(code, {"__builtins__": {}}, {**_FUNCS, **loc})  # noqa: S307
        except TypeError:            # None이 산술에 섞이면 결과도 NULL
            return None
        except ZeroDivisionError:
            return None

    run.refs = refs                  # type: ignore[attr-defined]
    return run


# ── 수식 → polars 식 (벡터화) ────────────────────────────────
#
# 행마다 파이썬 함수를 부르면 수백만 행에서 몇 분씩 걸린다. 가능한 수식은
# polars 식으로 번역해 한 번에 계산하고, 번역이 안 되는 것만 행 단위로
# 떨어뜨린다(_compile_expr가 None을 반환).

def _horizontal_std(cols: list[pl.Expr]) -> pl.Expr:
    """NULL을 빼고 계산하는 표본표준편차(n-1) — 엑셀 STDEV와 동일."""
    n = pl.sum_horizontal([c.is_not_null().cast(pl.Int32) for c in cols])
    mean = pl.sum_horizontal([c.fill_null(0.0) for c in cols]) / n
    sq = pl.sum_horizontal([
        pl.when(c.is_not_null()).then((c - mean) ** 2).otherwise(0.0)
        for c in cols])
    return pl.when(n > 1).then((sq / (n - 1)).sqrt()).otherwise(None)


def _horizontal_sum(cols: list[pl.Expr]) -> pl.Expr:
    """인자가 전부 NULL이면 0이 아니라 NULL — 행 단위 엔진(_null_skip)과 동치.

    polars의 sum_horizontal은 NULL만 있는 행을 0으로 준다. 그대로 두면 측정이
    아예 없는 wafer의 ADDP가 '0'이라는 멀쩡한 값으로 plot·표·Δ에 들어간다.
    """
    n = pl.sum_horizontal([c.is_not_null().cast(pl.Int32) for c in cols])
    return pl.when(n > 0).then(pl.sum_horizontal(cols)).otherwise(None)


def _finite(e: pl.Expr) -> pl.Expr:
    """비유한값(inf/NaN)을 NULL로. 행 단위 엔진과 값을 맞추는 장치.

    polars는 0으로 나누면 예외 대신 ±inf를 준다. 최종 결과만 걸러 내면
    `Exp({A}/{B})`처럼 **inf가 함수를 거치며 멀쩡한 값으로 둔갑**하는 경우를
    놓친다(exp(-inf) = 0.0). 그래서 나눗셈·거듭제곱 자리에서 바로 끊는다.
    """
    return pl.when(e.is_finite()).then(e).otherwise(None)


def _vec_exp(e: pl.Expr) -> pl.Expr:
    """지수 — 넘치면 inf가 아니라 NULL. 행 단위 엔진(_exp)과 같은 값이어야 한다."""
    x = e.exp()
    return pl.when(x.is_finite()).then(x).otherwise(None)


_VEC_UNARY = {
    # 행 단위 _BASE_FUNCS와 **같은 의미**여야 한다(test_reformatter_vector가 고정).
    "abs": lambda e: e.abs(),
    "sqrt": lambda e: pl.when(e >= 0).then(e.sqrt()).otherwise(None),
    "log10": lambda e: pl.when(e > 0).then(e.log10()).otherwise(None),
    "log": lambda e: pl.when(e > 0).then(e.log10()).otherwise(None),   # 상용로그
    "ln": lambda e: pl.when(e > 0).then(e.log()).otherwise(None),      # 자연로그
    "exp": _vec_exp,
}
_VEC_NARY = {
    "min": pl.min_horizontal,
    "max": pl.max_horizontal,
    "avg": pl.mean_horizontal,
    "sum": _horizontal_sum,
    "std": _horizontal_std,
    "stdev": _horizontal_std,
    "stddev": _horizontal_std,
}


def _compile_expr(src: str, available: set[str]) -> pl.Expr | None:
    """수식을 polars 식으로. 번역 불가면 None."""
    refs = _REF.findall(src)
    if any(r not in available for r in refs):
        return None
    py = _REF.sub(lambda m: f"__v{refs.index(m.group(1))}", src)
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError:
        return None

    def walk(node):
        if isinstance(node, ast.BinOp):
            a, b = walk(node.left), walk(node.right)
            if a is None or b is None:
                return None
            op = type(node.op)
            if op is ast.Add:
                return a + b
            if op is ast.Sub:
                return a - b
            if op is ast.Mult:
                return a * b
            if op is ast.Div:
                return _finite(a / b)      # 0으로 나눈 inf를 여기서 끊는다
            if op is ast.Pow:
                return _finite(a ** b)
            return None
        if isinstance(node, ast.UnaryOp):
            v = walk(node.operand)
            if v is None:
                return None
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Constant):
            return pl.lit(float(node.value))
        if isinstance(node, ast.Name):
            if not node.id.startswith("__v"):
                return None
            return pl.col(refs[int(node.id[3:])])
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return None
            fn = node.func.id.lower()
            args = [walk(a) for a in node.args]
            if any(a is None for a in args):
                return None
            if fn in _VEC_UNARY and len(args) == 1:
                return _VEC_UNARY[fn](args[0])
            if fn in _VEC_NARY:
                return _VEC_NARY[fn](args)
            return None
        return None

    expr = walk(tree.body)
    if expr is None:
        return None
    # 0으로 나누면 inf가 나온다 — 행 단위 엔진과 맞춰 NULL로
    return pl.when(expr.is_finite()).then(expr).otherwise(None)


# ── Std 그룹 표본표준편차 (요청 ⑤) ───────────────────────────
def _std_calls(formula: str) -> list[tuple[int, int, list[str]]]:
    """바깥쪽 Std 호출마다 (시작, 끝, 인자 문자열들). 괄호가 안 맞으면 []."""
    calls = []
    pos = 0
    while (m := _STD_HEAD.search(formula, pos)):
        depth, i, args, cur = 1, m.end(), [], m.end()
        while i < len(formula) and depth:
            c = formula[i]
            if c == "{":                       # ALIAS 안의 괄호·쉼표는 무시
                i = formula.find("}", i)
                if i < 0:
                    return []
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c == "," and depth == 1:
                args.append(formula[cur:i].strip())
                cur = i + 1
            i += 1
        if depth:
            return []
        args.append(formula[cur:i - 1].strip())
        calls.append((m.start(), i, args))
        pos = i
    return calls


def _std_needs(formula: str, wide_cols: set[str],
               keys: tuple[str, ...] = STD_KEYS) -> list[list[str]]:
    """수식에서 그룹 std로 바꿀 Std 호출들의 인자 식 목록(나오는 순서대로).

    인자는 `{A}`뿐 아니라 `Abs({A})`·`{A}/{B}` 같은 식이어도 된다 — 칩마다
    먼저 계산한 뒤 묶음 산포를 낸다. 6키가 wide에 없거나, 인자가 참조하는
    컬럼이 하나라도 없거나, Std 안에 Std가 있으면 []를 돌려 수식을 그대로
    둔다 — 일부만 골라 바꾸면 미측정 참조 폴백의 의미가 조용히 달라진다
    (전부 또는 무).
    """
    if not all(k in wide_cols for k in keys):
        return []
    calls: list[list[str]] = []
    for _, _, args in _std_calls(formula):
        if any(not a or _STD_HEAD.search(a) for a in args):
            return []
        if not all(r in wide_cols for a in args for r in _REF.findall(a)):
            return []
        calls.append(args)
    return calls


def _std_name(taken: set[str], prefix: str = "__std") -> str:
    i = 0
    while f"{prefix}{i}" in taken:
        i += 1
    return f"{prefix}{i}"


def _eval_rows(wide: pl.DataFrame, formula: str) -> pl.Series:
    """수식을 행마다 계산한다 — 벡터로 번역되지 않는 수식의 폴백."""
    fn = compile_formula(formula)
    cols = [wide[a] if a in wide.columns else pl.Series([None] * len(wide))
            for a in fn.refs]
    vals = [fn(dict(zip(fn.refs, t))) for t in zip(*cols)] if cols \
        else [fn({})] * len(wide)
    return pl.Series(vals, dtype=pl.Float64)


def _eval_formula(wide: pl.DataFrame, formula: str) -> tuple[pl.Expr | pl.Series, bool]:
    """(값, 벡터로 계산했나). 벡터 번역이 안 되면 행 단위로 떨어진다."""
    expr = _compile_expr(formula, set(wide.columns))
    if expr is not None:
        return expr, True
    return _eval_rows(wide, formula), False


def _std_scaffold(wide: pl.DataFrame, args: list[str], name: str,
                  keys: tuple[str, ...] = STD_KEYS) -> pl.DataFrame:
    """인자 식들을 STD_KEYS 그룹으로 묶어 표본 std(n-1)를 만들고 wide에 붙인다. (⑤)

    `{A}`가 아닌 인자(`Abs({A})` 등)는 칩마다 먼저 계산해 임시 컬럼으로 두고,
    묶음 계산이 끝나면 지운다.

    polars std는 NaN을 null처럼 취급하지 않으므로 행 단위 엔진(_std_sample)과
    값을 맞추려면 먼저 fill_nan(None)으로 없애야 한다. group_by std는 NULL
    무시·유효값<2면 NULL이라 _std_sample과 같은 의미다.

    join은 **nulls_equal=True**여야 한다 — group_by는 NULL 키를 한 묶음으로
    세는데 기본 join은 NULL 키를 서로 다르다고 봐서, 키 하나(온도·step_seq)만
    비어도 그룹 전체의 결과가 NULL이 되고 drop_nulls가 item을 통째로 지웠다.
    """
    cols: list[str] = []
    temps: list[str] = []
    for a in dict.fromkeys(args):      # 같은 인자를 두 번 적어도 select가 터지지 않게
        m = re.fullmatch(r"\{([^{}]+)\}", a)
        if m:
            cols.append(m.group(1))
            continue
        tmp = _std_name(set(wide.columns), "__stdarg")
        val, _ = _eval_formula(wide, a)
        wide = wide.with_columns(val.cast(pl.Float64).alias(tmp))
        cols.append(tmp)
        temps.append(tmp)
    cols = list(dict.fromkeys(cols))
    melt = (
        wide.select([*keys, *cols])
        .with_columns([pl.col(c).cast(pl.Float64).fill_nan(None) for c in cols])
        .unpivot(index=list(keys), on=cols,
                 variable_name="__var", value_name="__val")
    )
    agg = melt.group_by(keys).agg(pl.col("__val").std())
    wide = wide.join(agg.rename({"__val": name}), on=list(keys), how="left",
                     nulls_equal=True)
    return wide.drop(temps) if temps else wide


def _std_substitute(formula: str, names: list[str]) -> str:
    """Std 호출을 {__stdN} 참조로 바꾼 수식 (호출 순서대로)."""
    out, last = [], 0
    for (start, end, _), name in zip(_std_calls(formula), names):
        out += [formula[last:start], "{" + name + "}"]
        last = end
    return "".join(out) + formula[last:]


# ── 적용 (long → long, 피벗 전) ───────────────────────────────
def apply(rf: Reformatter, df: pl.DataFrame,
          on_progress=None) -> pl.DataFrame:
    """long(키…, item_id, value)에 스케일→절대값→ALIAS 개명→ADDP 추가.

    청크 단위(키 완결)로 호출된다. 반환도 long이며 item_id는 ALIAS가 된다.
    on_progress(단계명, 완료, 전체)로 진행 상황을 알린다.
    """
    def tick(stage: str, done: int = 0, total: int = 0) -> None:
        if on_progress:
            on_progress(stage, done, total)

    if "et_value" in df.columns and "value" not in df.columns:
        df = df.rename({"et_value": "value"})   # 원본 컬럼명 → 내부 표준
    tick("REAL 스케일·절대값", 0, 0)
    keymap = {r.itemid: r for r in rf.reals()}
    df = df.filter(pl.col("item_id").is_in(list(keymap)))
    scale = pl.col("item_id").replace_strict(
        {k: v.scale for k, v in keymap.items()}, default=1.0
    )
    absf = pl.col("item_id").replace_strict(
        {k: v.absolute for k, v in keymap.items()}, default=False
    )
    df = df.with_columns(
        value=pl.when(absf)
        .then((pl.col("value") * scale).abs())
        .otherwise(pl.col("value") * scale),
        item_id=pl.col("item_id").replace_strict(
            {k: v.alias for k, v in keymap.items()}
        ),
    )

    addps = rf.addps()
    if not addps:
        tick("완료", 1, 1)
        return df

    tick("피벗", 0, 0)
    key_cols = [c for c in df.columns if c not in ("item_id", "value")]
    wide = df.pivot(on="item_id", index=key_cols, values="value",
                    aggregate_function="first")
    reals = [c for c in wide.columns if c not in key_cols]

    wide, std_cols = _compute_addps(wide, addps, STD_KEYS, tick)
    if std_cols:
        # 스캐폴드 컬럼이 출력 item_id로 새지 않게 역피벗 전에 지운다
        wide = wide.drop(*std_cols)
    wide = _fill_cross_seq(wide, df, key_cols, reals, addps)

    tick("역피벗", 0, 0)
    out = wide.unpivot(index=key_cols, variable_name="item_id", value_name="value")
    return out.drop_nulls("value")


#: ADDP를 seq 병합 프레임에서 계산할 때 무시하는 키 — 분석 로딩의 §10.1 병합
#: (`compat.select_sql`)과 같은 규칙이다. 나머지 키(die 좌표·온도·step_id·
#: site_cnt·lot·wafer)가 다르면 여전히 다른 측정점이다.
SEQ_KEYS = ("step_seq", "tkout_time")


def _latest_expr(key_cols: list[str]) -> pl.Expr:
    """(나머지 키 + step_seq)마다 tkout_time이 가장 늦은 행 — 읽기의 retest QUALIFY와 같다."""
    if "tkout_time" not in key_cols:
        return pl.lit(True)
    part = [c for c in key_cols if c != "tkout_time"]
    t = pl.col("tkout_time")
    tmax = t.max().over(part)
    # DuckDB는 DESC에서 NULL을 뒤로 둔다 — 시각이 전부 NULL일 때만 그 행이 최신
    return pl.when(tmax.is_null()).then(True).otherwise(t == tmax).fill_null(False)


def _cross_seq_aliases(df: pl.DataFrame, addps: list[Rule]) -> set[str]:
    """seq 하나로는 풀 수 없는 ADDP — 참조 항목을 **모두** 담은 step_seq가 없다.

    ADDP를 참조하면 그 ADDP의 seq를 따라간다(연쇄). 데이터에 없는 참조는
    판정에서 뺀다 — 미측정 참조 폴백(`Max({A},{없음})`)은 예전처럼 A의 seq에서 푼다.
    """
    seqs: dict[str, set] = {
        k: set(v) for k, v in
        df.group_by("item_id").agg(pl.col("step_seq").unique()).iter_rows()}
    cross: set[str] = set()
    for r in addps:
        refs = list(dict.fromkeys(_REF.findall(r.formula)))
        if any(a in cross for a in refs):
            cross.add(r.alias)
            continue
        have = [seqs[a] for a in refs if a in seqs]
        if not have:
            continue
        common = set.intersection(*have)
        if common:
            seqs[r.alias] = common
        else:
            cross.add(r.alias)
    return cross


def _fill_cross_seq(wide: pl.DataFrame, df: pl.DataFrame, key_cols: list[str],
                    reals: list[str], addps: list[Rule]) -> pl.DataFrame:
    """step_seq를 넘나드는 ADDP를 살린다(§10.1).

    행 단위 계산은 `step_seq`·시각까지 키로 둔 행에서 돌기 때문에, seq 1의 항목과
    seq 2의 항목을 한 수식에 쓰면 두 값이 한 행에 있는 적이 없다. 결과는 전부
    NULL이 되어 `drop_nulls`가 그 item을 **적재 전에** 지웠고(표의 CAT1이 통째로
    비고 그 item을 축으로 쓴 산점도가 비던 원인), `Std()`는 seq마다 일부 값만으로
    계산돼 조용히 틀린 값이 남았다.

    그런 ADDP(`_cross_seq_aliases`)만 분석 로딩과 같은 규칙 — seq마다 최신
    retest → seq·시각만 다른 행을 한 측정점으로, 항목마다 NULL이 아닌 첫 값 —
    으로 합친 프레임에서 계산해 **die의 최신 행마다** 같은 값으로 붙인다. REAL은
    원래 seq에 그대로 남고, 이 ADDP는 그 die의 모든 seq 행에 있어 어느 seq로
    조회해도 보인다. seq 안에서 풀리는 ADDP는 예전과 한 글자도 다르지 않다.
    """
    if "step_seq" not in key_cols or wide["step_seq"].n_unique() <= 1:
        return wide                     # 합칠 것이 없다 — 행 단위 결과가 곧 답
    cross = _cross_seq_aliases(df, addps)
    aliases = [a for a in dict.fromkeys(r.alias for r in addps) if a in cross]
    if not aliases:
        return wide
    by = [c for c in key_cols if c not in SEQ_KEYS]
    latest = _latest_expr(key_cols)
    merged = (wide.filter(latest)
              .sort("step_seq", nulls_last=True, maintain_order=True)
              .group_by(by, maintain_order=True)
              .agg(pl.col(reals).first(ignore_nulls=True)))
    merged, _ = _compute_addps(merged, addps,
                               tuple(k for k in STD_KEYS if k not in SEQ_KEYS))
    log.info("ADDP %d개는 step_seq를 합쳐 계산했습니다(seq를 넘나드는 수식): %s",
             len(aliases), ", ".join(aliases[:10]))
    tmp = {a: f"__seq_{a}" for a in aliases}
    return (wide.with_columns(latest.alias("__latest"))
            .join(merged.select([*by, *aliases]).rename(tmp), on=by,
                  how="left", nulls_equal=True, maintain_order="left")
            .with_columns([pl.when(pl.col("__latest")).then(pl.col(tmp[a]))
                           .otherwise(None).alias(a) for a in aliases])
            .drop(["__latest", *tmp.values()]))


def _compute_addps(wide: pl.DataFrame, addps: list[Rule],
                   std_keys: tuple[str, ...], tick=None
                   ) -> tuple[pl.DataFrame, set[str]]:
    """ADDP를 시트 행 순서대로 계산해 wide에 붙인다. (결과, Std 스캐폴드 컬럼)."""
    n_slow = 0
    n_std = 0
    std_cache: dict[frozenset[str], str] = {}
    std_cols: set[str] = set()
    for i, rule in enumerate(addps, 1):   # 행 순서 = 계산 순서
        if tick:
            tick(f"ADDP {rule.alias}", i, len(addps))
        formula = rule.formula
        need = _std_needs(formula, set(wide.columns), std_keys)
        if need:
            # Std 호출을 그룹 std 스캐폴드 컬럼 참조로 바꾼다.
            # 같은 인자 조합은 캐시해서 한 번만 만든다.
            names: list[str] = []
            for args in need:
                key = frozenset(re.sub(r"\s+", "", a) for a in args)
                name = std_cache.get(key)
                if name is None:
                    name = _std_name(set(wide.columns) | std_cols)
                    wide = _std_scaffold(wide, args, name, std_keys)
                    std_cache[key] = name
                    std_cols.add(name)
                names.append(name)
            formula = _std_substitute(formula, names)
            n_std += 1
        elif tick and _std_calls(formula):
            log.info(
                "ADDP %s: Std()를 그룹 단위로 바꿀 수 없어(6키·인자 컬럼이 "
                "데이터에 없거나 Std 안에 Std) 예전처럼 행 단위로 계산합니다",
                rule.alias)
        val, fast = _eval_formula(wide, formula)  # 벡터 경로 (거의 전부)
        n_slow += not fast                        # 그 외는 행 단위 폴백
        val = val.abs() if rule.absolute else val
        wide = wide.with_columns(val.cast(pl.Float64).alias(rule.alias))

    # 로그는 행 단위 계산(첫 번째 호출)에서만 — 병합 재계산까지 세면 두 번 찍힌다
    if tick and n_slow:
        log.info("ADDP %d개 중 %d개는 행 단위로 계산했습니다", len(addps), n_slow)
    if tick and n_std:
        log.info("ADDP %d개 중 %d개는 Std()를 그룹 표본표준편차로 계산했습니다",
                 len(addps), n_std)
    return wide, std_cols
