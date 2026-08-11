"""리포메터 — CATEGORY·ITEMID·ALIAS·ABSOLUTE·SCALE FACTOR·ADDP FORM·
UNIT·SPECLOW·SPECHIGH·TARGET.

처리 순서(확정 사양):
  1) REAL item에 SCALE FACTOR 적용  (수식 계산 **전**)
  2) ABSOLUTE == 'Y' 이면 절대값
  3) ADDP를 시트의 **행 순서대로** 계산 — 아래 행은 위 행의 ADDP를 참조할 수
     있고 그 반대는 검증 오류. 순서 규칙이 곧 순환참조 차단이다.

수식 문법: ``{ALIAS}`` 참조, 사칙연산, 그리고 화이트리스트 함수만.
eval()은 쓰지 않는다 — ast로 파싱해 직접 걷는다.
``Std({A},{B},...)`` 는 인자들을 각각 하나의 포인트로 보는 산포:
키 8개(root_lot~total_site_cnt) 단위, 엑셀 STDEV와 같은 표본표준편차(n-1),
NULL 인자는 빼고 계산한다.
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
    w: float | None = None   # 옵션 기하 컬럼 "W" (폭)
    l: float | None = None   # 옵션 기하 컬럼 "L" (길이)  # noqa: E741 — 헤더명 "L" 확정


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

    rf = Reformatter()
    raw = read_sheet(path, sheet)

    missing = [c for c in COLUMNS if c not in raw.columns]
    if missing:
        rf.errors.append(ReformatterError(0, "", f"컬럼 누락: {', '.join(missing)}"))
        return rf

    for i, row in enumerate(raw.iter_rows(named=True), start=2):
        alias = str(row["ALIAS"] or "").strip()
        if not alias:
            continue
        rf.rules.append(Rule(
            category=str(row["CATEGORY"] or "REAL").strip().upper(),
            itemid=str(row["ITEMID"] or "").strip(),
            alias=alias,
            absolute=str(row["ABSOLUTE"] or "N").strip().upper() == "Y",
            scale=_num(row["SCALE FACTOR"]) or 1.0,
            formula=str(row["ADDP FORM"] or "").strip(),
            unit=str(row["UNIT"] or "").strip(),
            speclow=_num(row["SPECLOW"]),
            spechigh=_num(row["SPECHIGH"]),
            target=_num(row["TARGET"]),
            row=i,
            # W/L은 옵션 컬럼 — 시트에 있을 때만 읽는다(없으면 구파일과 동일).
            w=_num(row.get("W")) if "W" in raw.columns else None,
            l=_num(row.get("L")) if "L" in raw.columns else None,
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


_BASE_FUNCS = {
    # 파이썬식 이름 → 구현. SQL로 내릴 땐 min→least 등 매핑 주의(계획서 Q8).
    "Std":   _std_sample,
    "Avg":   _null_skip(lambda xs: sum(xs) / len(xs)),
    "Sum":   _null_skip(sum),
    "Min":   _null_skip(min),
    "Max":   _null_skip(max),
    "Abs":   _abs,
    "Sqrt":  lambda v: None if v is None or v < 0 else math.sqrt(v),
    "Log10": lambda v: None if v is None or v <= 0 else math.log10(v),
    "Ln":    lambda v: None if v is None or v <= 0 else math.log(v),
}

# 엑셀에서 ABS(), abs() 처럼 아무렇게나 써도 통하도록 대소문자 별칭을 깐다.
_FUNCS: dict = {}
for _n, _f in _BASE_FUNCS.items():
    _FUNCS[_n] = _f
    _FUNCS[_n.upper()] = _f
    _FUNCS[_n.lower()] = _f

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


_VEC_UNARY = {
    "abs": lambda e: e.abs(),
    "sqrt": lambda e: pl.when(e >= 0).then(e.sqrt()).otherwise(None),
    "log10": lambda e: pl.when(e > 0).then(e.log10()).otherwise(None),
    "ln": lambda e: pl.when(e > 0).then(e.log()).otherwise(None),
}
_VEC_NARY = {
    "min": pl.min_horizontal,
    "max": pl.max_horizontal,
    "avg": pl.mean_horizontal,
    "sum": _horizontal_sum,
    "std": _horizontal_std,
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
                return a / b
            if op is ast.Pow:
                return a ** b
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

    n_slow = 0
    for i, rule in enumerate(addps, 1):   # 행 순서 = 계산 순서
        tick(f"ADDP {rule.alias}", i, len(addps))
        expr = _compile_expr(rule.formula, set(wide.columns))
        if expr is not None:                      # 벡터 경로 (거의 전부)
            e = expr.abs() if rule.absolute else expr
            wide = wide.with_columns(e.cast(pl.Float64).alias(rule.alias))
            continue
        n_slow += 1                               # 행 단위 폴백
        fn = compile_formula(rule.formula)
        cols = [wide[a] if a in wide.columns else pl.Series([None] * len(wide))
                for a in fn.refs]
        vals = [fn(dict(zip(fn.refs, t))) for t in zip(*cols)] if cols \
               else [fn({})] * len(wide)
        v = pl.Series(rule.alias, vals, dtype=pl.Float64)
        wide = wide.with_columns(v.abs() if rule.absolute else v)

    if n_slow:
        log.info("ADDP %d개 중 %d개는 행 단위로 계산했습니다", len(addps), n_slow)
    tick("역피벗", 0, 0)
    out = wide.unpivot(index=key_cols, variable_name="item_id", value_name="value")
    return out.drop_nulls("value")
