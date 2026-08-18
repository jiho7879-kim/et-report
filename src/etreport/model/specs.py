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


#: plot 하나가 점을 무엇으로 찍을지 — 템플릿의 Mode 열과 같은 값.
#: site=측정점 그대로, 나머지는 (lot, wafer) 집계 한 점.
POINT_MODES = ("site", "avg", "med", "std")

#: plot 종류 — 템플릿의 `Type` 열과 같은 값. **화면 콤보와 템플릿이 같은 목록을
#: 본다**(둘이 갈리면 템플릿으로 저장했다 다시 열 때 종류가 바뀐다).
#: table은 표 전용 페이지라 사용자가 고르는 종류가 아니다(pptgen이 따로 만든다).
PLOT_TYPES = ("scatter", "box", "trend")
PLOT_TYPE_LABELS = {
    "scatter": "산점도",
    "box":     "boxplot (범주별 분포)",
    "trend":   "기하 trend (W·L)",
}

GEOM_COLUMNS = ("W", "L")   # trend chart X축 식별자 — 리포메터 WIDTH/LENGTH 컬럼에 매핑


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
    type: str = "scatter"            # scatter | box | trend | table(표 전용 페이지)
    mode: str = "site"               # site | avg | med | std — 그릴 데이터 레벨
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
    """table 템플릿 한 행. item_id는 리포메터 ALIAS와 동일.

    **CAT 개수는 고정하지 않는다**(§3.3 확정) — 템플릿에 CAT4·CAT5를 더 두면
    그만큼 계층이 늘어난다. `cats[0]`(=CAT1)은 표를 나누는 기준이고 나머지는
    표 안의 계층이다.
    """
    item_id: str
    cats: list[str] = field(default_factory=list)   # CAT1, CAT2, … 번호순

    @property
    def cat1(self) -> str:
        """표를 나누는 기준 — CAT1 하나 = 표 하나 = 시트 하나 = PPT 한 장."""
        return self.cats[0] if self.cats else ""

    @property
    def subcats(self) -> list[str]:
        """표 안의 계층 (CAT2 이후)."""
        return self.cats[1:]


@dataclass
class ReportSpec:
    """Report 이름 하나에 대응하는 전체 구성."""
    report: str
    pages: list[PageSpec] = field(default_factory=list)
    table_rows: list[TableRowSpec] = field(default_factory=list)
    #: 표 안 계층 열의 이름 (CAT2 이후). 개수는 템플릿이 정한다(§3.3).
    cat_names: list[str] = field(default_factory=list)

    def table_names(self) -> list[str]:
        out: list[str] = []
        for r in self.table_rows:
            if r.cat1 not in out:
                out.append(r.cat1)
        return out
