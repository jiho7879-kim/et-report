"""polars 프레임 한 장을 그냥 보여 주는 창.

이력·목록처럼 "표만 보면 되는" 자리가 여럿이라(이상치 필터 이력, 제외 이력 등)
매번 QTableWidget을 다시 채우는 코드를 쓰지 않으려고 둔다. 편집은 없다 —
보여 주기만 하고, 필요하면 [복사]로 클립보드에 TSV로 가져간다.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from etreport.ui.widgets.cards import GhostButton

#: 한 번에 그릴 최대 행. 이보다 많으면 앞쪽만 그리고 그렇다고 적는다 —
#: 수만 행을 QTableWidget에 밀어 넣으면 창이 뜨는 데만 몇 초가 걸린다.
MAX_ROWS = 2000


class FrameDialog(QDialog):
    def __init__(self, df, title: str, parent=None) -> None:
        super().__init__(parent)
        self.df = df
        self.setWindowTitle(title)
        self.resize(820, 560)

        v = QVBoxLayout(self)
        head = QHBoxLayout()
        shown = min(df.height, MAX_ROWS)
        lab = QLabel(f"{df.height:,}행"
                     + (f" (앞 {shown:,}행만 표시)" if df.height > shown else ""))
        lab.setObjectName("hint")
        head.addWidget(lab)
        head.addStretch(1)
        b = GhostButton("복사")
        b.setToolTip("표 전체를 TSV로 클립보드에 복사합니다(엑셀에 바로 붙습니다)")
        b.clicked.connect(self._copy)
        head.addWidget(b)
        v.addLayout(head)

        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setColumnCount(len(df.columns))
        self.table.setHorizontalHeaderLabels(list(df.columns))
        self.table.setRowCount(shown)
        self.table.verticalHeader().setVisible(False)
        for r, rec in enumerate(df.head(shown).iter_rows()):
            for c, val in enumerate(rec):
                self.table.setItem(
                    r, c, QTableWidgetItem("" if val is None else str(val)))
        self.table.resizeColumnsToContents()
        v.addWidget(self.table, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _copy(self) -> str:
        """**전체**를 TSV로. 화면에 앞부분만 그렸어도 복사는 전부다."""
        lines = ["\t".join(self.df.columns)]
        for rec in self.df.iter_rows():
            lines.append("\t".join("" if v is None else str(v) for v in rec))
        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        return text
