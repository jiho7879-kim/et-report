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
from etreport.ui.tabs.common import StaleMixin, fit_table, on_combo
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
        self._collapsed: set[str] = set()      # 접어 둔 CAT1 (표를 다시 만들어도 유지)
        self._cards: dict[str, Card] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 14)
        outer.setSpacing(10)

        bar = QHBoxLayout()
        self.btn_build = QPushButton("표 만들기")
        self.btn_build.setToolTip("CAT1마다 wafer 표를 만듭니다 (Ctrl+Enter)")
        self.btn_build.clicked.connect(self.rebuild)
        bar.addWidget(self.btn_build)
        self.agg = QComboBox()
        self.agg.addItems(["평균", "산포 (wafer 내)", "그룹별 평균",
                           "그룹별 wafer"])
        self.agg.setToolTip("평균·산포는 wafer마다 한 열,\n"
                            "그룹별 평균은 그룹마다 한 열,\n"
                            "그룹별 wafer는 wafer 열을 그룹으로 묶어 정렬합니다.")
        on_combo(self.agg, self.mark_stale)
        bar.addWidget(self.agg)
        self.chk_delta = QCheckBox("Δ vs REF")
        self.chk_delta.toggled.connect(self.mark_stale)
        bar.addWidget(self.chk_delta)
        # CAT1 표는 하나가 화면을 다 먹는다 — 접어 두고 필요한 것만 편다
        b_fold = GhostButton("모두 접기")
        b_fold.clicked.connect(lambda: self._fold_all(True))
        bar.addWidget(b_fold)
        b_open = GhostButton("모두 펼치기")
        b_open.clicked.connect(lambda: self._fold_all(False))
        bar.addWidget(b_open)
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

    # ── 접기 ─────────────────────────────────────────────────
    def _attach_collapse(self, card, cat1: str) -> None:
        """CAT1 카드에 ▾/▸ 토글을 달고 접힘 상태를 기억한다."""
        card.make_collapsible(cat1 in self._collapsed)
        card._collapse_btn.toggled.connect(
            lambda on, c=cat1: self._remember(c, on))
        self._cards[cat1] = card

    def _remember(self, cat1: str, on: bool) -> None:
        (self._collapsed.add if on else self._collapsed.discard)(cat1)

    def _fold_all(self, on: bool) -> None:
        """표를 다시 만들지 않는다 — 보이기만 바꾼다."""
        names = list(self._cards) or (
            self.state.report.table_names() if self.state.report else [])
        for cat1 in names:
            self._remember(cat1, on)
            card = self._cards.get(cat1)
            if card is not None:
                card.set_collapsed(on)

    # ── 지연 계산 (규약은 tabs/common.StaleMixin) ────────────
    def refresh(self) -> None:
        self.rebuild()

    AGG_MODES = ("avg", "std", "gavg", "gwafer")

    def agg_mode(self) -> str:
        i = self.agg.currentIndex()
        return self.AGG_MODES[i] if 0 <= i < len(self.AGG_MODES) else "avg"

    def agg_label(self) -> str:
        return {"avg": "평균", "std": "wafer 내 std (n−1)",
                "gavg": "그룹별 평균",
                "gwafer": "그룹별 wafer (평균)"}[self.agg_mode()]

    # ── 계산 ─────────────────────────────────────────────────
    def _stats(self):
        """wafer별 통계를 한 번에 — item×wafer 반복 필터링 없음."""
        from etreport.model.aggregate import ref_values, wafer_stats
        st = self.state
        aliases = [r.item_id for r in st.report.table_rows]
        agg = self.agg_mode()
        ws = wafer_stats(st.data, st.hidden(), aliases,
                         agg if agg in ("avg", "std") else "avg")
        ref = {}
        if self.chk_delta.isChecked():
            g = st.ref_group()
            ref = ref_values(st.data, st.hidden(), g.gid if g else None,
                             aliases, agg)
        return ws, ref, agg

    def rebuild(self) -> None:
        """[표 만들기] — 집계는 group_by 1회라 빠르지만 표 위젯 생성이 있으므로
        버튼을 잠그고 대기 커서를 띄운다."""
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.btn_build.setEnabled(False)
        try:
            self._rebuild()
        finally:
            self.btn_build.setEnabled(True)
            QApplication.restoreOverrideCursor()

    def _rebuild(self) -> None:
        import time

        from etreport.model.aggregate import offspec
        t0 = time.monotonic()
        while self.vbox.count():
            it = self.vbox.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._cards.clear()          # 곧 파괴될 카드를 붙잡고 있지 않도록
        st = self.state
        if st.report is None or st.data is None:
            lab = QLabel(
                "Table 템플릿이 아직 없습니다.\n"
                "왼쪽에서 Table 템플릿을 고른 뒤 [적용](F5)을 누르면 "
                "CAT1마다 표가 한 장씩 만들어집니다."
                if st.data is not None else
                "아직 불러온 데이터가 없습니다.\n"
                "왼쪽에서 DB를 고르고 [적용](F5)을 누르세요.")
            lab.setObjectName("emptyHint")
            lab.setAlignment(Qt.AlignCenter)
            self.vbox.addWidget(lab)
            self.lbl_state.setText("")
            return

        ws, ref, agg = self._stats()
        header = st.wafer_columns()
        delta = self.chk_delta.isChecked()
        if agg in ("gavg", "gwafer"):         # 열 구성이 다르다 — 표를 그대로 그린다
            self._rebuild_groups(t0)
            return

        for cat1 in st.report.table_names():
            rows = [r for r in st.report.table_rows if r.cat1 == cat1]
            card = Card(cat1, f"{len(rows)}개 item")
            cbtn = GhostButton("복사")
            cbtn.clicked.connect(lambda _=False, c=cat1: self._copy(c))
            xbtn = GhostButton("xlsx")
            xbtn.clicked.connect(lambda _=False, c=cat1: self._export([c]))
            card.head.addWidget(cbtn)
            card.head.addWidget(xbtn)

            # 라벨 열 = CAT2…CATn + item. **개수는 템플릿이 정한다**(§3.3)
            cat_names = list(getattr(st.report, "cat_names", []) or [])
            n_cat = max((len(r.subcats) for r in rows), default=0)
            cat_heads = [cat_names[i] if i < len(cat_names) else f"CAT{i + 2}"
                         for i in range(n_cat)]
            n_lab = n_cat + 1
            ncol = n_lab + sum(len(w) for _, w in header)
            t = QTableWidget(len(rows), ncol)
            t.setObjectName("sumTable")
            heads = [*cat_heads, "item"]
            for lot, wl in header:
                heads += [f"{lot}\n{w}" for w in wl]
            t.setHorizontalHeaderLabels(heads)
            t.verticalHeader().setVisible(False)
            t.setAlternatingRowColors(True)
            t.setEditTriggers(QTableWidget.NoEditTriggers)

            for ri, rs in enumerate(rows):
                rule = st.rf.by_alias.get(rs.item_id)
                rv = ref.get(rs.item_id) if delta else None
                labels = [*rs.subcats, *[""] * (n_cat - len(rs.subcats)),
                          rs.item_id]
                for ci, txt in enumerate(labels):
                    t.setItem(ri, ci, QTableWidgetItem(txt))
                ci = n_lab
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
                f"{self.agg_label()}"
                f" · 제외 {len(st.excluded)}점 반영"
                f"{' · Δ = REF 대비' if delta else ''}"
                " · 붉은 셀은 SPECLOW/SPECHIGH 이탈")
            cap.setObjectName("hint")
            card.body.addWidget(cap)
            self._attach_collapse(card, cat1)      # 내용을 다 넣은 뒤에
            self.vbox.addWidget(card)

        self.mark_fresh()
        self.lbl_state.setText(
            f"{len(st.report.table_names())}개 표 · {time.monotonic() - t0:.2f}초")

    def _rebuild_groups(self, t0: float) -> None:
        """그룹별 평균 표 — 값은 build_table(단일 진실)이 만든 것을 그대로 그린다."""
        import time

        from etreport.export.excel import build_table
        st = self.state
        opt = self._options()
        for cat1 in st.report.table_names():
            td = build_table(st, cat1, opt)
            card = Card(cat1, f"{len(td.rows)}개 item")
            cbtn = GhostButton("복사")
            cbtn.clicked.connect(lambda _=False, c=cat1: self._copy(c))
            card.head.addWidget(cbtn)
            xbtn = GhostButton("xlsx")
            xbtn.clicked.connect(lambda _=False, c=cat1: self._export([c]))
            card.head.addWidget(xbtn)
            labels = td.labels()
            if opt.agg == "gwafer":
                # 그룹(첫 줄) + lot·wafer(둘째 줄) — 값이 어느 wafer의 것인지 보인다
                cols = [*labels, *[f"{lot}\n{w}" for lot, ws in td.header_lots
                                   for w in ws]]
            else:                        # gavg — 그룹 이름만
                cols = [*labels, *[w for _lot, ws in td.header_lots for w in ws]]
            t = QTableWidget(len(td.rows), len(cols))
            t.setObjectName("sumTable")
            t.setHorizontalHeaderLabels(cols)
            t.verticalHeader().setVisible(False)
            t.setAlternatingRowColors(True)
            t.setEditTriggers(QTableWidget.NoEditTriggers)
            for r, row in enumerate(td.rows):
                for c, text in enumerate(td.label_values(row)):
                    t.setItem(r, c, QTableWidgetItem(text))
                for c, (v, off) in enumerate(zip(row["values"], row["offspec"]),
                                             start=len(labels)):
                    it = QTableWidgetItem(fmt_value(v, opt.delta_vs_ref))
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    if off:
                        it.setBackground(QColor("#ffecee"))
                        it.setForeground(QColor("#d70015"))
                    t.setItem(r, c, it)
            fit_table(t)
            card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            card.body.addWidget(t)
            cap = QLabel(opt.session_caption)
            cap.setObjectName("hint")
            card.body.addWidget(cap)
            self._attach_collapse(card, cat1)      # 내용을 다 넣은 뒤에
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
        agg = self.agg_mode()
        return SummaryOptions(
            agg=agg,
            delta_vs_ref=self.chk_delta.isChecked(),
            session_caption=(
                f"{st.report.report if st.report else ''} · "
                f"{self.agg_label()}"
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
