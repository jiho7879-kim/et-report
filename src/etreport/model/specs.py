"""표시 스펙 — 화면(pyqtgraph)과 PPT(matplotlib)가 공유하는 단일 진실.

자릿수 규칙(확정): |값| < 1 → 소수 3자리 · 1 ≤ |값| ≤ 10 → 2자리 · > 10 → 1자리
"""
from __future__ import annotations

from dataclasses import dataclass, field


def fmt_value(v: float | None, delta: bool = False) -> str:
    if v is None:
        return ""
    a = abs(v)
    if a < 1:
        s = f"{v:.3f}"
    elif a <= 10:
        s = f"{v:.2f}"
    else:
        s = f"{v:.1f}"
    return f"+{s}" if delta and v >= 0 else s


@dataclass
class GroupStyle:
    gid: str
    name: str
    color: str = "#0071e3"
    symbol: str = "o"        # o s t d +   (pyqtgraph/mpl 공용 매핑은 render에서)
    size: int = 6
    visible: bool = True
    ref: bool = False


@dataclass
class PlotSpec:
    """plot 템플릿 한 행(또는 탐색 화면 상태)과 1:1.

    x·y는 쉼표 구분 다중 ALIAS 허용 — 순서대로 xy쌍이 되어 겹쳐 그린다.
    축 스케일·범위는 auto가 기본이고 plot별 오버라이드를 가진다.
    """
    title: str = ""
    x: str = ""
    y: str = ""
    x_name: str = ""
    y_name: str = ""
    type: str = "scatter"            # scatter | table | box(예정)
    ref_band: bool = False
    logx_mode: str = "auto"          # auto | log | linear
    logy_mode: str = "auto"
    range_mode: str = "auto"         # auto | manual
    xmin: float | None = None
    xmax: float | None = None
    ymin: float | None = None
    ymax: float | None = None

    def pairs(self) -> list[tuple[str, str]]:
        xs = [t.strip() for t in self.x.split(",") if t.strip()]
        ys = [t.strip() for t in self.y.split(",") if t.strip()]
        n = max(len(xs), len(ys))
        return [(xs[min(i, len(xs) - 1)], ys[min(i, len(ys) - 1)])
                for i in range(n)]


@dataclass
class PageSpec:
    number: int
    title: str = ""                  # title1
    slots: list[PlotSpec | None] = field(default_factory=lambda: [None] * 6)


@dataclass
class TableRowSpec:
    """table 템플릿 한 행. item_id는 리포메터 ALIAS와 동일."""
    item_id: str
    cat1: str
    cat2: str = ""
    cat3: str = ""


@dataclass
class ReportSpec:
    """Report 이름 하나에 대응하는 전체 구성."""
    report: str
    pages: list[PageSpec] = field(default_factory=list)
    table_rows: list[TableRowSpec] = field(default_factory=list)

    def table_names(self) -> list[str]:
        out: list[str] = []
        for r in self.table_rows:
            if r.cat1 not in out:
                out.append(r.cat1)
        return out
