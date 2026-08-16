"""커버리지 다이얼로그 — lot마다 무엇이 빠졌는지 표로 보여 준다(§9.2).

판정은 전부 `model/coverage.py`가 한다. 여기는 그 결과를 늘어놓고 복사만 시킨다 —
같은 규칙을 화면 코드에 다시 적으면 [적용] 로그와 이 표의 말이 갈린다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from etreport.model.coverage import POINT_TOLERANCE, CoverageReport
from etreport.ui.widgets.cards import GhostButton

HEADERS = ("lot", "wafer", "item", "없는 item", "측정 조건",
           "wafer당 포인트", "기준 대비")


def report_tsv(rep: CoverageReport) -> str:
    """엑셀에 그대로 붙는 TSV. 없는 item은 **전부** 적는다(표는 접어 보여 준다)."""
    lines = ["\t".join(HEADERS)]
    for r in rep.rows:
        lines.append("\t".join([
            r.lot,
            str(r.wafers),
            str(r.items),
            ", ".join(r.missing_items),
            str(len(r.conditions)),
            f"{r.points_median:g}",
            "기준" if r.is_base else
            ("" if r.point_ratio is None else f"{r.point_ratio:.2f}"),
        ]))
    return "\n".join(lines)


class CoverageDialog(QDialog):
    def __init__(self, rep: CoverageReport, parent=None) -> None:
        super().__init__(parent)
        self.rep = rep
        self.setWindowTitle("lot 커버리지")
        self.resize(860, 460)
        v = QVBoxLayout(self)

        if not rep.rows:
            v.addWidget(QLabel("읽어 들인 데이터가 없습니다 — 먼저 [적용]을 누르세요"))
        elif len(rep.rows) < 2:
            v.addWidget(QLabel(
                f"lot이 {rep.base_lot} 하나뿐입니다 — 비교할 대상이 없습니다"))
        else:
            head = QLabel(
                f"기준 lot <b>{rep.base_lot}</b> (item이 가장 많은 lot)에 견주어 "
                f"봅니다. 포인트 수는 기준의 "
                f"{1 - POINT_TOLERANCE:.0%}~{1 + POINT_TOLERANCE:.0%}를 벗어나면 "
                f"표시합니다.")
            head.setWordWrap(True)
            v.addWidget(head)

        self.table = QTableWidget(len(rep.rows), len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch)
        self._fill()
        v.addWidget(self.table, 1)

        # 경고를 놓치지 않게 [적용] 로그와 같은 문구를 아래에 그대로 깔아 둔다
        warn = QLabel("\n".join(rep.lines()) or "어긋나는 곳이 없습니다")
        warn.setObjectName("hint")
        warn.setWordWrap(True)
        warn.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(warn)

        row = QHBoxLayout()
        b = GhostButton("표 복사")
        b.setToolTip("엑셀에 그대로 붙는 TSV로 복사합니다 (없는 item 전체 포함)")
        b.clicked.connect(self._copy)
        row.addWidget(b)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        v.addLayout(row)

    def _fill(self) -> None:
        for i, r in enumerate(self.rep.rows):
            missing = ", ".join(r.missing_items[:8])
            if len(r.missing_items) > 8:
                missing += f" 외 {len(r.missing_items) - 8}개"
            ratio = ("기준" if r.is_base else
                     "-" if r.point_ratio is None else f"{r.point_ratio:.2f}배")
            for c, text in enumerate((r.lot, str(r.wafers), str(r.items),
                                      missing, str(len(r.conditions)),
                                      f"{r.points_median:g}", ratio)):
                it = QTableWidgetItem(text)
                if c and c != 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                # 어긋난 칸만 눈에 띄게 — 색만으로 알리지 않도록 값도 함께 적힌다
                if (c == 3 and r.missing_items) or (c == 6 and r.point_off):
                    it.setToolTip("기준 lot과 다릅니다")
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if c == 4 and (r.cond_missing or r.cond_extra):
                    it.setToolTip(
                        "없는 조건: " + (", ".join(r.cond_missing) or "-")
                        + "\n여기에만: " + (", ".join(r.cond_extra) or "-"))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(i, c, it)
        self.table.resizeColumnsToContents()

    def _copy(self) -> None:
        QApplication.clipboard().setText(report_tsv(self.rep))
        from etreport.ui.widgets.toast import toast
        toast(self, "커버리지 표를 복사했습니다")
