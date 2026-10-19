"""제외 목록 — 손으로 뺀 점을 lot·wafer·시각별로 보고 골라 되살린다.

Ctrl+Z는 마지막 동작부터 차례로만 되돌린다. 몇 단계 전에 뺀 점은 그 사이의
제외까지 모두 풀어야 닿았다 — 리포트 화면에서는 뺀 점이 흔적 없이 사라지므로
다시 찍을 수도 없다. 목록은 `loader.exclusion_groups`가 만들고, 되살리는 일은
워크스페이스(`restore`)가 한다 — 사이드카·알림을 화면 조각이 따로 다루지 않게.
이상치 필터가 걸러 낸 점은 여기 없다(필터를 끄면 돌아온다).
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from etreport.model.state import AppState
from etreport.ui.widgets.cards import GhostButton

HEADERS = ("lot", "wafer", "측정점", "사유", "뺀 시각")


class ExclusionDialog(QDialog):
    def __init__(self, state: AppState, restore: Callable[[list[str]], None],
                 parent=None) -> None:
        super().__init__(parent)
        self.state, self.restore = state, restore
        self.setWindowTitle("제외 목록")
        self.resize(720, 460)
        v = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setObjectName("hint")
        self.head.setWordWrap(True)
        v.addWidget(self.head)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_sel = GhostButton("고른 줄 되살리기")
        self.btn_sel.clicked.connect(lambda: self._restore(self._selected()))
        self.btn_all = GhostButton("전부 되살리기")
        self.btn_all.clicked.connect(lambda: self._restore(
            [k for keys in self._keys for k in keys]))
        row.addWidget(self.btn_sel)
        row.addWidget(self.btn_all)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        v.addLayout(row)
        self._fill()

    def _fill(self) -> None:
        from etreport.data.loader import exclusion_groups
        g = exclusion_groups(self.state)
        self._keys = g["keys"].to_list()
        self.table.setRowCount(g.height)
        for i, r in enumerate(g.iter_rows(named=True)):
            cells = (r["lot"] or "(읽지 않은 lot)", r["wafer"] or "",
                     f"{r['n']:,}", r["reason"], r["at"])
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if c == 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
        self.table.resizeColumnsToContents()
        n = sum(g["n"]) if g.height else 0
        self.head.setText(
            f"손으로 뺀 측정점 {n:,}개 — 줄을 골라 되살리면 모든 plot·표·PPT에 "
            "다시 들어갑니다. 이상치 필터가 걸러 낸 점은 여기 없습니다(필터를 "
            "끄면 돌아옵니다)." if n else "손으로 뺀 점이 없습니다.")
        self._sync_buttons()

    def _selected(self) -> list[str]:
        rows = {ix.row() for ix in self.table.selectionModel().selectedRows()}
        return [k for i in sorted(rows) for k in self._keys[i]]

    def _sync_buttons(self) -> None:
        self.btn_sel.setEnabled(bool(self.table.selectionModel().selectedRows()))
        self.btn_all.setEnabled(bool(self._keys))

    def _restore(self, keys: list[str]) -> None:
        if keys:
            self.restore(keys)
            self._fill()
