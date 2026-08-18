"""fab tracking(`fab.f_fab_tracking`) — split 실험 lot 식별과 자동 그룹핑 근거.

ET 계측(`eds.f_et_test`)과 **별개 소스**다. 여기서 얻는 것은 "이 lot의 어느
step에서 조건이 갈렸는가"이고, 그 결과를 `model/split.SplitMatrix`로 넘겨
기존 factor 그룹핑·혼입 감지를 그대로 쓴다(그룹핑 로직을 새로 만들지 않는다).

핵심 규칙(확정):
  - `line_id = 'KFBK'` 기본, `ein_ecn_no IS NOT NULL`인 lot을 먼저 본다
    (split 실험이 걸린 lot이라는 뜻).
  - **조건 비교 컬럼은 area에 따라 다르다** — `area='PHOTO'`면 `reticle_id`
    (=포토의 recipe), 그 외는 `ppid`.
  - 유효 wafer 안에서 step별 조건을 비교해 **서로 다른 값이 나오는 step만**
    골라낸다. 그 step들이 곧 실험 축(factor) 후보다.
  - 컬럼명은 **원본 그대로** 유지한다(추출 경로에서 축약·변형 금지).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

import polars as pl

log = logging.getLogger(__name__)

TABLE = "fab.f_fab_tracking"
DEFAULT_LINE = "KFBK"

#: 원본 컬럼명 그대로 — 축약하지 않는다.
COLUMNS: list[str] = [
    "part_id", "process_id", "step_seq", "root_lot_id", "wafer_id", "area",
    "tkout_time", "foup_id", "eqp_model", "eqp_id", "unit_id", "chamber_id",
    "ppid", "reticle_id", "ein_ecn_no", "line_id",
]

PHOTO_AREA = "PHOTO"
#: PHOTO는 recipe(=reticle_id), 그 외 area는 ppid로 조건을 본다.
RECIPE_COL = "reticle_id"
PPID_COL = "ppid"


def _in(col: str, vals: list[str]) -> str:
    quoted = ", ".join("'" + str(v).replace("'", "''") + "'" for v in vals)
    return f"{col} IN ({quoted})"


def build_tracking_sql(lots: list[str] | None = None,
                       line_id: str = DEFAULT_LINE,
                       d_from: date | None = None,
                       d_to: date | None = None,
                       ecn_only: bool = True,
                       table: str = TABLE,
                       process_ids: list[str] | None = None,
                       part_ids: list[str] | None = None) -> str:
    """fab tracking 조회 SQL. lot을 주면 그 lot만, 없으면 기간으로 좁힌다.

    기간은 ET 추출과 같은 규칙으로 **문자열 리터럴**로 넘긴다(§4.1) — Impala가
    TIMESTAMP로 캐스팅한다.

    `process_ids`·`part_ids`는 분석 중인 DuckDB에서 읽어 온 값을 그대로 받는다
    (`data/lotcontext.py`) — 손으로 적으면 오타 하나에 조회가 비고, 그때는
    "데이터가 없다"와 구별되지 않는다. **비우면 조건을 걸지 않는다**(예전과 동일).
    """
    where = [f"line_id = '{line_id}'"]
    if ecn_only:
        where.append("ein_ecn_no IS NOT NULL")     # split 실험이 걸린 lot 우선
    if lots:
        where.append(_in("root_lot_id", lots))
    if process_ids:
        where.append(_in("process_id", process_ids))
    if part_ids:
        where.append(_in("part_id", part_ids))
    if d_from is not None:
        where.append(f"tkout_time >= '{d_from:%Y-%m-%d} 00:00:00'")
    if d_to is not None:
        from datetime import timedelta
        hi = d_to + timedelta(days=1)
        where.append(f"tkout_time <  '{hi:%Y-%m-%d} 00:00:00'")
    body = "\n  AND  ".join(where)
    cols = ", ".join(COLUMNS)
    return f"SELECT {cols}\nFROM   {table}\nWHERE  {body}"


def fetch(sql: str) -> pl.DataFrame:
    """bdq 조회 — 추출 경로와 같은 정규화(Categorical·문자열 시각)를 태운다."""
    import bigdataquery as bdq  # 사내 패키지 — 지연 import

    from etreport.data.extractor import normalize_categoricals
    return normalize_categoricals(pl.from_pandas(bdq.getData(sql)))


def condition_expr() -> pl.Expr:
    """조건 값 컬럼 — PHOTO면 reticle_id, 그 외는 ppid(확정 규칙)."""
    return (pl.when(pl.col("area").cast(pl.Utf8).str.to_uppercase() == PHOTO_AREA)
            .then(pl.col(RECIPE_COL).cast(pl.Utf8))
            .otherwise(pl.col(PPID_COL).cast(pl.Utf8))
            .alias("condition"))


def step_key_expr() -> pl.Expr:
    """step 식별자 — `process_id` 기준(없으면 step_seq)."""
    return (pl.col("process_id").cast(pl.Utf8)
            .fill_null(pl.col("step_seq").cast(pl.Utf8))
            .alias("step_id"))


def wafer_conditions(df: pl.DataFrame) -> pl.DataFrame:
    """(root_lot_id, wafer_id, step_id) → condition 한 장으로 정리.

    같은 step을 여러 번 지난 경우(재작업)는 **가장 늦은 tkout_time**을 쓴다 —
    실제로 그 wafer가 마지막에 받은 조건이 실험 조건이다.
    """
    if df.is_empty():
        return pl.DataFrame(schema={"root_lot_id": pl.Utf8, "wafer_id": pl.Utf8,
                                    "step_id": pl.Utf8, "condition": pl.Utf8})
    out = df.with_columns(step_key_expr(), condition_expr())
    if "tkout_time" in out.columns:
        out = out.sort("tkout_time")
    return (out.group_by(["root_lot_id", "wafer_id", "step_id"], maintain_order=True)
            .agg(pl.col("condition").drop_nulls().last().alias("condition"))
            .sort(["root_lot_id", "wafer_id", "step_id"]))


def split_steps(df: pl.DataFrame, min_wafers: int = 2) -> list[str]:
    """조건이 **갈리는 step**만 골라낸다 — 이게 실험 축 후보다.

    lot 안에서 wafer마다 조건이 다르면 그 step에서 split이 걸린 것이다.
    모든 wafer가 같은 조건이면 실험과 무관하므로 뺀다(비교해도 의미가 없다).
    멀티 lot이면 **lot 하나라도** 갈리는 step을 후보로 본다.
    """
    cond = wafer_conditions(df)
    if cond.is_empty():
        return []
    per_lot = (cond.group_by(["root_lot_id", "step_id"])
               .agg(pl.col("condition").n_unique().alias("n"),
                    pl.len().alias("wafers")))
    hit = per_lot.filter((pl.col("n") > 1) & (pl.col("wafers") >= min_wafers))
    return sorted(set(hit["step_id"].to_list()))


def to_split_matrix(df: pl.DataFrame, baseline: str | None = None,
                    steps: list[str] | None = None,
                    baseline_lot: str = ""):
    """fab tracking → `SplitMatrix` (기존 그룹핑·혼입 감지를 그대로 쓴다).

    `steps`를 주지 않으면 조건이 갈리는 step만 싣는다. baseline을 주지 않으면
    **각 step에서 가장 많은 wafer가 받은 조건**을 기준(REF)으로 삼는다 —
    split 실험은 보통 기준 조건 wafer가 가장 많다.

    `baseline_lot`을 주면 **그 lot 안에서 step마다** 다수 조건을 뽑아 기준으로
    삼는다(§9.2). lot마다 POR이 다를 수 있는 멀티 lot에서, 어느 lot을 기준
    삼을지는 사람이 정해야 하는 판단이다.
    """
    from etreport.model.split import BASELINE_DEFAULT, SplitMatrix

    cond = wafer_conditions(df)
    picked = steps if steps is not None else split_steps(df)
    if cond.is_empty() or not picked:
        return SplitMatrix(steps=[], wide=pl.DataFrame(
            schema={"lot": pl.Utf8, "wafer": pl.Utf8}), baseline=BASELINE_DEFAULT)

    cond = cond.filter(pl.col("step_id").is_in(picked))
    wide = (cond.pivot(on="step_id", index=["root_lot_id", "wafer_id"],
                       values="condition", aggregate_function="first")
            .rename({"root_lot_id": "lot", "wafer_id": "wafer"})
            .sort(["lot", "wafer"]))
    base = baseline or _majority_code(cond)
    steps_in = [c for c in wide.columns if c not in ("lot", "wafer")]
    # step별 기준은 빈칸을 메우기 **전에** 뽑는다 — 메운 뒤에 세면 안 적힌 칸이
    # 기준 쪽에 표를 던져 다수 조건이 뒤집힐 수 있다.
    codes = (SplitMatrix.codes_from_lot(wide, steps_in, baseline_lot)
             if baseline_lot else {})
    wide = wide.with_columns([pl.col(s).fill_null(codes.get(s, base))
                              for s in steps_in])
    return SplitMatrix(steps=steps_in, wide=wide, baseline=base,
                       baseline_codes=codes,
                       baseline_lot=baseline_lot if codes else "")


def _majority_code(cond: pl.DataFrame, lot: str = "", step: str = "") -> str:
    """가장 흔한 조건 코드 — 기준(REF)으로 삼는다.

    `lot`·`step`을 주면 그 범위 안에서만 센다. 좁힌 결과가 비면 전체 다수
    조건으로 물러난다 — 기준 lot이 그 step을 지나지 않았을 수 있다.
    """
    sub = cond
    if lot:
        sub = sub.filter(pl.col("root_lot_id").cast(pl.Utf8) == str(lot))
    if step:
        sub = sub.filter(pl.col("step_id").cast(pl.Utf8) == str(step))
    if sub.is_empty() and (lot or step):
        sub = cond
    top = (sub.filter(pl.col("condition").is_not_null())
           .group_by("condition").len()
           .sort(["len", "condition"], descending=[True, False]))
    return str(top["condition"][0]) if not top.is_empty() else "Base"


def summarize(df: pl.DataFrame) -> str:
    """화면·LoadReport에 넣을 한 줄 요약."""
    cond = wafer_conditions(df)
    lots = cond["root_lot_id"].n_unique() if not cond.is_empty() else 0
    steps = split_steps(df)
    return (f"fab tracking  lot {lots} · 조건이 갈리는 step {len(steps)}개"
            + (f": {', '.join(steps[:5])}" if steps else ""))


# ── 이름 붙인 컬럼 뽑기 ──────────────────────────────────────
#: `source`로 고를 수 있는 원본 컬럼. 앞의 AUTO는 예전부터 쓰던 규칙 그대로다.
AUTO_SOURCE = "(자동)"
SOURCE_CHOICES: tuple[str, ...] = (
    AUTO_SOURCE, PPID_COL, RECIPE_COL, "eqp_id", "eqp_model", "unit_id",
    "chamber_id", "foup_id", "step_seq", "area", "ein_ecn_no", "part_id",
    "process_id",
)
#: `step`을 비우면 step을 가리지 않는다는 뜻.
ANY_STEP = ""
_NAME_BAD = re.compile(r"[^0-9A-Za-z가-힣_.\-]+")


@dataclass(frozen=True)
class TrackColumn:
    """fab tracking에서 뽑아 분석 프레임에 붙일 컬럼 **하나**.

    예전에는 `process_id` 값이 그대로 표의 컬럼 머리글이 됐다 — 이름을 정할 자리가
    없어서 `1400` 같은 코드가 축 이름으로 나가고, 같은 step에서 recipe와 설비를
    함께 보고 싶어도 컬럼을 하나밖에 만들 수 없었다. 그래서 **이름 · 어느 step ·
    원본 컬럼**을 사용자가 각각 정하게 한다.

    name   : 분석 프레임에 붙을 컬럼 이름 (plot 축·표 범주로 그대로 쓰인다)
    source : 원본 컬럼. `AUTO_SOURCE`면 예전 규칙(PHOTO=reticle_id, 그 외=ppid)
    step   : 그 step의 값만 뽑는다. 비우면(`ANY_STEP`) step을 가리지 않는다
    """
    name: str
    source: str = AUTO_SOURCE
    step: str = ANY_STEP

    def label(self) -> str:
        src = "자동(PHOTO=recipe·그 외=ppid)" if self.source == AUTO_SOURCE \
            else self.source
        return f"{self.name} ← {src}" + (f" @ {self.step}" if self.step else "")


def safe_name(raw: str, taken: set[str] | None = None) -> str:
    """사용자가 적은 이름을 컬럼으로 쓸 수 있게 다듬는다.

    공백·따옴표가 든 이름은 polars·DuckDB·PPT 표에서 서로 다른 방식으로 새는데,
    그걸 각자 처리하게 두면 어디선가 조용히 어긋난다. 여기서 한 번만 정리한다.
    빈 이름과 중복은 뒤에 번호를 붙여 살린다 — 이름 때문에 컬럼을 버리지 않는다.
    """
    name = _NAME_BAD.sub("_", str(raw or "").strip()).strip("_") or "track"
    taken = taken or set()
    if name not in taken:
        return name
    i = 2
    while f"{name}_{i}" in taken:
        i += 1
    return f"{name}_{i}"


def source_expr(source: str) -> pl.Expr:
    """원본 컬럼 하나를 문자열 값으로. `AUTO_SOURCE`는 예전 규칙 그대로."""
    if source == AUTO_SOURCE:
        return condition_expr().alias("_v")
    return pl.col(source).cast(pl.Utf8).alias("_v")


def derive(df: pl.DataFrame, cols: list[TrackColumn]) -> pl.DataFrame:
    """조회 결과 → `lot | wafer | <이름들…>` 한 장.

    같은 (lot, wafer, step)을 여러 번 지난 경우(재작업)는 `wafer_conditions`와
    같은 규칙으로 **가장 늦은 tkout_time**을 쓴다 — 그 wafer가 마지막에 받은
    조건이 실제 조건이다. 규칙을 두 곳에 두지 않으려고 정렬만 여기서 다시 한다.
    """
    empty = pl.DataFrame(schema={"lot": pl.Utf8, "wafer": pl.Utf8})
    if df is None or df.is_empty() or not cols:
        return empty
    src = df.with_columns(step_key_expr())
    if "tkout_time" in src.columns:
        src = src.sort("tkout_time")

    out: pl.DataFrame | None = None
    for c in cols:
        if c.source != AUTO_SOURCE and c.source not in src.columns:
            log.warning("fab tracking에 %s 컬럼이 없어 '%s'를 건너뜁니다",
                        c.source, c.name)
            continue
        sub = src
        if c.step:
            sub = sub.filter(pl.col("step_id").cast(pl.Utf8) == str(c.step))
        if sub.is_empty():
            log.warning("'%s': step %s에 해당하는 행이 없습니다", c.name, c.step)
            continue
        part = (sub.with_columns(source_expr(c.source))
                .group_by(["root_lot_id", "wafer_id"], maintain_order=True)
                .agg(pl.col("_v").drop_nulls().last().alias(c.name))
                .rename({"root_lot_id": "lot", "wafer_id": "wafer"}))
        out = part if out is None else out.join(part, on=["lot", "wafer"],
                                                how="full", coalesce=True)
    return (out.sort(["lot", "wafer"]) if out is not None else empty)


def suggest_columns(df: pl.DataFrame,
                    steps: list[str] | None = None) -> list[TrackColumn]:
    """기본 제안 — 조건이 갈리는 step마다 자동 규칙 컬럼 하나.

    이렇게 두면 창을 열자마자 보이는 것이 **지금까지와 같은 결과**다(컬럼 이름이
    step 코드). 거기서 이름을 고치거나 줄을 더하는 것이 사용자의 몫이 된다 —
    기본값을 바꿔서 놀라게 하지 않는다.
    """
    picked = steps if steps is not None else split_steps(df)
    names: set[str] = set()
    out: list[TrackColumn] = []
    for s in picked:
        n = safe_name(s, names)
        names.add(n)
        out.append(TrackColumn(name=n, source=AUTO_SOURCE, step=s))
    return out


def attach(data: pl.DataFrame, values: pl.DataFrame
           ) -> tuple[pl.DataFrame, list[str]]:
    """분석 프레임에 tracking 컬럼을 붙인다. (프레임, 붙은 이름들) 반환.

    조인 키는 (lot, wafer)이고 **표기를 정규화해서 붙인다**(`model/wafers`) —
    tracking은 `01`, ET DB는 `W01`로 적히는 일이 흔해서 그대로 조인하면 전부
    null이 된다. inline 계측(`metrology.attach`)과 같은 규칙이다.

    이미 같은 이름의 열이 있으면 **덮어쓴다** — 계측과 다른 점이다. 사용자가
    이름을 정해 다시 뽑는 흐름이라(조건을 고쳐 재조회) 덮이지 않으면 고친 결과가
    반영되지 않는다. 대신 예약 컬럼(key·lot·wafer·gid·step·temp·site)은 절대
    건드리지 않는다.
    """
    from etreport.data.loader import RESERVED
    from etreport.model import wafers

    if data is None or values is None or values.is_empty():
        return data, []
    names = [c for c in values.columns
             if c not in ("lot", "wafer") and c not in RESERVED]
    if not names:
        return data, []
    keys = [wafers.lot_key_expr("lot").alias("_lk"),
            wafers.wafer_key_expr("wafer").alias("_wk")]
    right = (values.select(["lot", "wafer", *names]).with_columns(keys)
             .drop(["lot", "wafer"])
             .unique(subset=["_lk", "_wk"], keep="first"))
    left = data.drop([c for c in names if c in data.columns])   # 다시 뽑으면 갱신
    joined = (left.with_columns(keys)
              .join(right, on=["_lk", "_wk"], how="left")
              .drop(["_lk", "_wk"]))
    return joined, names
