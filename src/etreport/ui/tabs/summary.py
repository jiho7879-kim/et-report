"""Summary 탭 — CAT1별 wafer 표.

표는 **[표 만들기]를 눌렀을 때만** 계산한다(보고 있어도 자동 계산하지 않는다).
복사·xlsx는 화면과 같은 숫자를 써야 하므로 export.excel.build_table 하나만 쓴다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from etreport.model.specs import fmt_value
from etreport.model.state import AppState, StateBus
from etreport.ui.tabs.common import StaleMixin, fit_table
from etreport.ui.widgets.cards import Card, GhostButton
from etreport.ui.widgets.worker import run_in_background


class SummaryTab(StaleMixin, QWidget):
    """표는 [표 만들기]를 눌렀을 때만 계산한다 (자동 재계산 없음)."""

    stale_button_attr = "btn_build"
    stale_label_attr = "lbl_state"
    stale_message = "변경됨 — [표 만들기]를 누르세요"
    auto_refresh = False           # 보고 있어도 자동으로 만들지 않는다

    def __init__(self, state: AppState, bus: StateBus, parent=None) -> None:
        super().__init__(parent)
        self.state, self.bus = state, bus
        self._stale = True
        outer = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.btn_build = QPushButton("표 만들기")
        self.btn_build.clicked.connect(self.rebuild)
        bar.addWidget(self.btn_build)
        self.agg = QComboBox()
        self.agg.addItems(["평균", "산포 (wafer 내)"])
        self.agg.currentIndexChanged.connect(self.mark_stale)
        bar.addWidget(self.agg)
        self.chk_delta = QCheckBox("Δ vs REF")
        self.chk_delta.toggled.connect(self.mark_stale)
        bar.addWidget(self.chk_delta)
        bar.addStretch(1)
        self.lbl_state = QLabel()
        self.lbl_state.setObjectName("hint")
        bar.addWidget(self.lbl_state)
        b = GhostButton("전체 xlsx 내보내기")
        b.clicked.connect(lambda: self._export(None))
        bar.addWidget(b)
        outer.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.host = QWidget()
        self.vbox = QVBoxLayout(self.host)
        self.vbox.setSpacing(14)
        self.vbox.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.host)
        outer.addWidget(self.scroll, 1)

        for sig in (bus.exclusion_changed, bus.groups_changed,
                    bus.data_changed, bus.report_changed):
            sig.connect(self.mark_stale)
        self.mark_stale()

    # ── 지연 계산 (규약은 tabs/common.StaleMixin) ────────────
    def refresh(self) -> None:
        self.rebuild()

    # ── 계산 ─────────────────────────────────────────────────
    def _stats(self):
        """wafer별 통계를 한 번에 — item×wafer 반복 필터링 없음."""
        from etreport.model.aggregate import ref_values, wafer_stats
        st = self.state
        aliases = [r.item_id for r in st.report.table_rows]
        agg = "avg" if self.agg.currentIndex() == 0 else "std"
        ws = wafer_stats(st.data, st.excluded, aliases, agg)
        ref = {}
        if self.chk_delta.isChecked():
            g = st.ref_group()
            ref = ref_values(st.data, st.excluded, g.gid if g else None,
                             aliases, agg)
        return ws, ref, agg

    def rebuild(self) -> None:
        import time

        from etreport.model.aggregate import offspec
        t0 = time.monotonic()
        while self.vbox.count():
            it = self.vbox.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        st = self.state
        if st.report is None or st.data is None:
            lab = QLabel("표를 만들려면 DB와 Table 템플릿(REPORT 선택)이 필요합니다"
                         if st.data is not None else
                         "데이터 없음 — 설정에서 DB를 고르고 [적용]을 누르세요")
            lab.setObjectName("hint")
            lab.setAlignment(Qt.AlignCenter)
            self.vbox.addWidget(lab)
            self.lbl_state.setText("")
            return

        ws, ref, agg = self._stats()
        header = st.wafer_columns()
        delta = self.chk_delta.isChecked()

        for cat1 in st.report.table_names():
            rows = [r for r in st.report.table_rows if r.cat1 == cat1]
            card = Card(cat1, f"{len(rows)}개 item")
            cbtn = GhostButton("복사")
            cbtn.clicked.connect(lambda _=False, c=cat1: self._copy(c))
            xbtn = GhostButton("xlsx")
            xbtn.clicked.connect(lambda _=False, c=cat1: self._export([c]))
            card.head.addWidget(cbtn)
            card.head.addWidget(xbtn)

            ncol = 3 + sum(len(w) for _, w in header)
            t = QTableWidget(len(rows), ncol)
            t.setObjectName("sumTable")
            heads = ["CAT2", "CAT3", "item"]
            for lot, wl in header:
                heads += [f"{lot}\n{w}" for w in wl]
            t.setHorizontalHeaderLabels(heads)
            t.verticalHeader().setVisible(False)
            t.setAlternatingRowColors(True)
            t.setEditTriggers(QTableWidget.NoEditTriggers)

            for ri, rs in enumerate(rows):
                rule = st.rf.by_alias.get(rs.item_id)
                rv = ref.get(rs.item_id) if delta else None
                for ci, txt in enumerate((rs.cat2, rs.cat3, rs.item_id)):
                    t.setItem(ri, ci, QTableWidgetItem(txt))
                ci = 3
                for lot, wl in header:
                    for wf in wl:
                        v = ws.get(rs.item_id, lot, wf)
                        off = (agg == "avg" and not delta and offspec(v, rule))
                        if delta and v is not None and rv is not None:
                            v -= rv
                        txt = fmt_value(v, delta)
                        ex = ws.ex(lot, wf)
                        if ex:
                            txt += f"  −{ex}"
                        item = QTableWidgetItem(txt)
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        if off:
                            item.setBackground(QColor("#ffecee"))
                            item.setForeground(QColor("#d70015"))
                        t.setItem(ri, ci, item)
                        ci += 1
            fit_table(t)
            card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            card.body.addWidget(t)
            cap = QLabel(
                f"{st.report.report} · "
                f"{'평균' if agg == 'avg' else 'wafer 내 std (n−1)'}"
                f" · 제외 {len(st.excluded)}점 반영"
                f"{' · Δ = REF 대비' if delta else ''}"
                " · 붉은 셀은 SPECLOW/SPECHIGH 이탈")
            cap.setObjectName("hint")
            card.body.addWidget(cap)
            self.vbox.addWidget(card)

        self.mark_fresh()
        self.lbl_state.setText(
            f"{len(st.report.table_names())}개 표 · {time.monotonic() - t0:.2f}초")

    def _copy(self, cat1: str) -> None:
        """화면 표와 같은 값을 클립보드로.

        예전에는 여기서 표를 다시 조립하느라 Δ vs REF가 빠져 화면과 다른 숫자가
        복사됐다. 이제 xlsx·PPT와 같은 build_table 경로만 쓴다.
        """
        from etreport.export.excel import build_table, to_tsv
        st = self.state
        if st.report is None or st.data is None:
            return
        td = build_table(st, cat1, self._options())
        QApplication.clipboard().setText(to_tsv(td, self._options()))
        QMessageBox.information(self, "복사됨", f"{cat1} 표를 클립보드에 복사했습니다")

    def _options(self):
        """화면 상태 → SummaryOptions (복사·xlsx·PPT가 공유)."""
        from etreport.export.excel import SummaryOptions
        st = self.state
        agg = "avg" if self.agg.currentIndex() == 0 else "std"
        return SummaryOptions(
            agg=agg,
            delta_vs_ref=self.chk_delta.isChecked(),
            session_caption=(
                f"{st.report.report if st.report else ''} · "
                f"{'평균' if agg == 'avg' else 'wafer 내 std (n−1)'}"
                f" · 제외 {len(st.excluded)}점 반영"
                + (" · Δ = REF 대비" if self.chk_delta.isChecked() else "")))

    def _export(self, cats) -> None:
        st = self.state
        names = cats or (st.report.table_names() if st.report else [])
        if not names or st.data is None:
            QMessageBox.warning(self, "xlsx 내보내기", "표로 만들 데이터가 없습니다")
            return
        out, _ = QFileDialog.getSaveFileName(
            self, "xlsx 저장", f"summary_{st.report.report}.xlsx",
            "Excel (*.xlsx)")
        if not out:
            return
        from etreport.export.excel import build_table, export_xlsx
        opt = self._options()
        tables = [build_table(st, n, opt) for n in names]   # 집계는 여기서(빠름)
        # Excel COM 왕복이 길어 UI가 멈추므로 워커로 넘긴다.
        run_in_background(
            self, "xlsx 내보내기", lambda: export_xlsx(tables, out, opt),
            done=lambda _: QMessageBox.information(self, "완료", f"저장됨:\n{out}"),
            needs_com=True)
