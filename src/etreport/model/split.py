"""실험 조건(split) 매트릭스 — 핵심 기능.

입력(최소 정형): lot·wafer별 step 조건 코드.
  wide : lot | wafer | M1 | M5 | …          (엔지니어가 쓰기 자연스러운 형태)
  long : lot | wafer | step_id | code
둘 다 받아 내부는 wide 하나로 정규화한다.

기능:
  - factor(step) 선택 → 조건 조합별 그룹 자동 생성 (baseline 코드는 REF)
  - 혼입(confound) 감지: 선택 factor 그룹 안에서 다른 step이 불균일하면 경고
  - 실험별 반복: 템플릿 페이지 × factor 수 만큼 PPT 페이지 확장 (pptgen에서)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import cycle

import polars as pl

from etreport.model.specs import GroupStyle

BASELINE_DEFAULT = "Base"
LABEL_SEP = " · "                # 표시용 구분자 (계산에는 쓰지 않는다)
PALETTE_OKABE = ["#E69F00", "#56B4E9", "#009E73", "#F0E442",
                 "#0072B2", "#D55E00", "#CC79A7", "#000000"]
SYMBOLS = ["o", "s", "t", "d", "+"]
REF_COLOR = "#8E8E93"


@dataclass
class Confound:
    group: str          # factor 조합 라벨
    step: str           # 섞여 있는 step
    codes: list[str]


def parse_split_text(text: str,
                     baseline: str = BASELINE_DEFAULT) -> SplitMatrix:
    """붙여넣은 표(탭·쉼표 구분) → SplitMatrix.

    엑셀에서 복사하면 탭 구분으로 붙는다. 엑셀이 없는 PC도 있고 붙여넣기가 더
    빠를 때도 많아 **파일과 동등한 입력 경로**로 둔다(§3.4 확정). 첫 줄이 머리글,
    구분자는 탭이 있으면 탭, 없으면 쉼표로 본다.
    """
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if len(lines) < 2:
        raise ValueError("머리글 한 줄과 자료 한 줄 이상이 필요합니다")
    sep = "\t" if "\t" in lines[0] else ","
    head = [h.strip() for h in lines[0].split(sep)]
    rows = []
    for ln in lines[1:]:
        # 빈 칸은 None으로 둔다 — ''로 두면 from_dataframe의 fill_null이 놓쳐서
        # 빈 칸이 'Base'가 아니라 **별개의 조건 코드**가 되고 가짜 그룹이 생긴다.
        cells: list[str | None] = [c.strip() or None for c in ln.split(sep)]
        cells += [None] * (len(head) - len(cells))      # 짧은 줄도 같은 취급
        rows.append(dict(zip(head, cells[:len(head)])))
    df = pl.DataFrame(rows, schema=dict.fromkeys(head, pl.Utf8))
    return SplitMatrix.from_dataframe(df, baseline)


def load_split_file(path: str, baseline: str = BASELINE_DEFAULT,
                    sheet: str | int = 0) -> SplitMatrix:
    """csv/tsv는 polars로, xlsx는 xlwings로 읽는다 (Excel은 xlwings만 — 제약)."""
    low = path.lower()
    if low.endswith((".csv", ".tsv", ".txt")):
        df = pl.read_csv(path, separator="\t" if low.endswith((".tsv", ".txt"))
                         else ",", infer_schema_length=0)
    else:
        from etreport.data.xlio import read_sheet
        df = read_sheet(path, sheet).cast(pl.Utf8, strict=False)
    return SplitMatrix.from_dataframe(df, baseline)


@dataclass
class SplitMatrix:
    steps: list[str] = field(default_factory=list)
    wide: pl.DataFrame | None = None      # lot, wafer, <steps...>
    baseline: str = BASELINE_DEFAULT

    # ── 로딩 ──────────────────────────────────────────────────
    @classmethod
    def from_dataframe(cls, df: pl.DataFrame,
                       baseline: str = BASELINE_DEFAULT) -> SplitMatrix:
        cols = {c.lower(): c for c in df.columns}
        lot = cols.get("lot") or cols.get("root_lot_id")
        waf = cols.get("wafer") or cols.get("wafer_id")
        if lot is None or waf is None:
            raise ValueError("lot / wafer 컬럼을 찾을 수 없습니다")
        df = df.rename({lot: "lot", waf: "wafer"})

        if "step_id" in cols and ("code" in cols or "condition" in cols):
            code = cols.get("code") or cols.get("condition")
            df = df.rename({cols["step_id"]: "step_id", code: "code"})
            wide = df.pivot(on="step_id", index=["lot", "wafer"],
                            values="code", aggregate_function="first")
        else:
            wide = df

        steps = [c for c in wide.columns if c not in ("lot", "wafer")]
        wide = wide.with_columns([pl.col(s).fill_null(baseline) for s in steps])
        return cls(steps=steps, wide=wide, baseline=baseline)

    # ── 그룹핑 ────────────────────────────────────────────────
    def combo_label(self, row: dict, factors: list[str]) -> str:
        return LABEL_SEP.join(str(row[f]) for f in factors)

    def combos_for(self, factors: list[str]
                   ) -> dict[tuple[str, ...], list[tuple[str, str]]]:
        """factor 코드 **튜플** → [(lot, wafer), …]. 등장 순서 유지.

        코드를 라벨로 합쳤다가 다시 쪼개면(라벨.split(" · ")) 코드 안에 구분자가
        들어간 순간 조용히 어긋난다. 내부에서는 항상 튜플을 들고 다니고,
        라벨은 보여줄 때만 만든다.
        """
        assert self.wide is not None
        out: dict[tuple[str, ...], list[tuple[str, str]]] = {}
        for row in self.wide.iter_rows(named=True):
            key = tuple(str(row[f]) for f in factors)
            out.setdefault(key, []).append((row["lot"], row["wafer"]))
        return out

    def label_of(self, codes: tuple[str, ...]) -> str:
        """factor 코드 튜플 → 화면에 보여줄 라벨."""
        return LABEL_SEP.join(codes)

    def styles_for(self, factors: list[str]) -> list[GroupStyle]:
        """조합별 GroupStyle — baseline 조합은 회색 REF, 나머지는 Okabe-Ito."""
        styles: list[GroupStyle] = []
        pal, sym = cycle(PALETTE_OKABE), cycle(SYMBOLS)
        for i, (codes, members) in enumerate(self.combos_for(factors).items()):
            is_ref = all(c == self.baseline for c in codes)   # 라벨을 되쪼개지 않는다
            label = self.label_of(codes)
            styles.append(GroupStyle(
                gid=f"x{i}", name=f"{label} ({len(members)})",
                color=REF_COLOR if is_ref else next(pal),
                symbol="d" if is_ref else next(sym),
                ref=is_ref,
            ))
        return styles

    # ── 혼입 감지 ─────────────────────────────────────────────
    def confounds(self, factors: list[str]) -> list[Confound]:
        """자동 그룹핑의 신뢰성 장치.

        선택한 factor로 나눈 각 그룹 안에서 나머지 step 코드가 2종 이상이면
        그 차이가 어느 step 때문인지 구분할 수 없다 — 반드시 경고한다.
        """
        assert self.wide is not None
        others = [s for s in self.steps if s not in factors]
        out: list[Confound] = []
        for combo in self.combos_for(factors):
            mask = pl.lit(True)
            for f, v in zip(factors, combo):
                mask = mask & (pl.col(f).cast(pl.Utf8) == v)
            sub = self.wide.filter(mask)
            for o in others:
                codes = sorted({str(c) for c in sub[o]})
                if len(codes) > 1:
                    out.append(Confound(group=self.label_of(combo),
                                        step=o, codes=codes))
        return out

    def assignment(self, factors: list[str]) -> dict[tuple[str, str], str]:
        """(lot, wafer) → gid. UI 그룹 배정과 fact 필터 양쪽에서 쓴다.

        키는 **정규화한 표기**다(`model/wafers`) — 실험 조건표에 `1`이라고
        적혀 있고 DB에는 `W01`로 들어 있어도 같은 wafer로 붙는다. 찾는 쪽도
        반드시 `wafers.key()`로 만든 키로 조회한다.
        """
        from etreport.model import wafers

        styles = self.styles_for(factors)
        out: dict[tuple[str, str], str] = {}
        for gid_style, members in zip(styles, self.combos_for(factors).values()):
            for lot, waf in members:
                out[wafers.key(lot, waf)] = gid_style.gid
        return out
