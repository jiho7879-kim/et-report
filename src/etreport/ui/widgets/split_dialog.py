"""실험 조건 다이얼로그 — 매트릭스 확인 · factor 선택 · 혼입 경고 · 그룹 미리보기."""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from etreport.model.state import AppState


class SplitDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("실험 조건")
        self.resize(760, 620)
        v = QVBoxLayout(self)

        v.addWidget(QLabel("lot·wafer별 step 조건 코드"))
        self.table = QTableWidget()
        v.addWidget(self.table, 1)

        v.addWidget(QLabel("실험 축 (factor) — 비교하려는 step을 고르세요"))
        self.chk_host = QWidget()
        self.chk_lay = QHBoxLayout(self.chk_host)
        self.chk_lay.setContentsMargins(0, 0, 0, 0)
        self.checks: dict[str, QCheckBox] = {}
        for step in state.split.steps:
            cb = QCheckBox(step)
            cb.setChecked(step in state.factors)
            cb.toggled.connect(self._factors_changed)
            self.checks[step] = cb
            self.chk_lay.addWidget(cb)
        self.chk_lay.addStretch(1)
        v.addWidget(self.chk_host)

        self.lbl_warn = QLabel()
        self.lbl_warn.setObjectName("warnBox")
        self.lbl_warn.setWordWrap(True)
        v.addWidget(self.lbl_warn)

        v.addWidget(QLabel("자동 생성될 그룹"))
        self.lbl_groups = QLabel()
        self.lbl_groups.setWordWrap(True)
        v.addWidget(self.lbl_groups)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._fill_table()
        self._refresh()

    def _fill_table(self) -> None:
        sm = self.state.split
        df = sm.wide
        cols = ["lot", "wafer", *sm.steps]
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(df.height)
        self.table.setHorizontalHeaderLabels(cols)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, rec in enumerate(df.iter_rows(named=True)):
            for c, col in enumerate(cols):
                it = QTableWidgetItem(str(rec[col]))
                if col in self.state.factors:
                    it.setBackground(QColor("#eaf3fd"))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()

    def _factors_changed(self) -> None:
        picked = [s for s, cb in self.checks.items() if cb.isChecked()]
        if not picked:                       # 최소 하나는 유지
            first = next(iter(self.checks.values()))
            first.blockSignals(True)
            first.setChecked(True)
            first.blockSignals(False)
            picked = [next(iter(self.checks))]
        self.state.factors = picked
        self._fill_table()
        self._refresh()

    def _refresh(self) -> None:
        sm, f = self.state.split, self.state.factors
        cf = sm.confounds(f)
        if cf:
            lines = ["<b>⚠ 혼입 주의</b>"]
            for c in cf[:4]:
                lines.append(
                    f"‘{c.group}’ 그룹 안에 <b>{c.step}</b>가 "
                    f"{len(c.codes)}종 섞여 있습니다 ({', '.join(c.codes)})")
            lines.append(
                f"<span style='opacity:.85'>이 상태로는 차이가 "
                f"{'·'.join(f)} 때문인지 {cf[0].step} 때문인지 구분할 수 없습니다. "
                f"{cf[0].step}도 factor로 추가하거나 "
                f"{cf[0].step}={sm.baseline}인 wafer만 남기세요.</span>")
            self.lbl_warn.setText("<br>".join(lines))
            self.lbl_warn.setProperty("level", "warn")
        else:
            self.lbl_warn.setText(
                "선택한 factor 외 조건이 모든 그룹에서 균일합니다 · 비교 가능")
            self.lbl_warn.setProperty("level", "ok")
        self.lbl_warn.style().unpolish(self.lbl_warn)
        self.lbl_warn.style().polish(self.lbl_warn)

        parts = []
        for st in sm.styles_for(f):
            parts.append(
                f"<span style='color:{st.color}'>■</span> {st.name}"
                f"{' · REF' if st.ref else ''}")
        self.lbl_groups.setText("&nbsp;&nbsp;&nbsp;".join(parts))
