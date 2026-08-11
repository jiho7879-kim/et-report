"""리포트 구성 탭 — 슬롯 배치·미리보기·PPT 생성.

슬롯을 드래그해 order를 바꾸고, [템플릿에 저장]으로 plot 템플릿 엑셀에 되쓴다.
미리보기는 [미리보기]를 누를 때(또는 이 화면을 보고 있을 때) 그린다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from etreport.model.state import AppState, StateBus
from etreport.ui.tabs.common import StaleMixin
from etreport.ui.widgets.cards import Card, GhostButton, row
from etreport.ui.widgets.plot_canvas import PlotCanvas
from etreport.ui.widgets.slot_grid import SlotFrame, swap_slots
from etreport.ui.widgets.worker import run_in_background


class ReportTab(StaleMixin, QWidget):
    """슬롯을 드래그로 재배치하고, 슬롯 안에서 바로 점을 제외할 수 있다.

    order(=슬롯 위치)를 화면에서 바꾸면 [템플릿에 저장]으로 엑셀에 되쓴다.
    """

    stale_button_attr = "btn_draw"

    def __init__(self, state: AppState, bus: StateBus, parent=None) -> None:
        super().__init__(parent)
        self.state, self.bus = state, bus
        self.page_idx = 0
        self.sel_slot: int | None = None
        self._frames: list[SlotFrame] = []
        self._canvases: dict[int, PlotCanvas] = {}   # 슬롯 index → 살아 있는 캔버스

        lay = QHBoxLayout(self)
        self.pages = QListWidget()
        self.pages.setFixedWidth(158)
        self.pages.currentRowChanged.connect(self._page_changed)
        lay.addWidget(self.pages)

        mid = QVBoxLayout()
        bar = QHBoxLayout()
        self.ed_title = QLineEdit()
        self.ed_title.textEdited.connect(self._title_changed)
        bar.addWidget(QLabel("페이지 제목"))
        bar.addWidget(self.ed_title, 1)
        b_save = GhostButton("템플릿에 저장")
        b_save.setToolTip("화면 배치를 plot 템플릿 엑셀에 되씁니다 (.bak 사본 생성)")
        b_save.clicked.connect(self._save_template)
        bar.addWidget(b_save)
        self.btn_draw = QPushButton("미리보기")
        self.btn_draw.clicked.connect(self.rebuild)
        bar.addWidget(self.btn_draw)
        b = QPushButton("PPT 생성")
        b.clicked.connect(self._ppt)
        bar.addWidget(b)
        mid.addLayout(bar)

        tools = QHBoxLayout()
        self.chk_pick = QCheckBox("클릭으로 점 제외")
        self.chk_pick.toggled.connect(lambda _: self.rebuild())
        tools.addWidget(self.chk_pick)
        self.chk_all = QCheckBox("모든 plot에서 함께 제외")
        self.chk_all.setChecked(True)
        self.chk_all.setToolTip(
            "켜면 같은 측정점이 모든 plot·표·PPT에서 함께 빠집니다.\n"
            "끄면 이 plot에서만 빠집니다.")
        self.chk_all.toggled.connect(self._all_toggled)
        tools.addWidget(self.chk_all)
        tools.addSpacing(14)
        hint = QLabel("슬롯을 끌어다 놓으면 자리가 바뀝니다")
        hint.setObjectName("hint")
        tools.addWidget(hint)
        tools.addStretch(1)
        self.lbl_excl = QLabel()
        self.lbl_excl.setObjectName("hint")
        tools.addWidget(self.lbl_excl)
        mid.addLayout(tools)

        self.slide = QFrame()
        self.slide.setObjectName("slide")
        sv = QVBoxLayout(self.slide)
        sv.setContentsMargins(16, 14, 16, 16)
        self.slide_title = QLabel()
        self.slide_title.setObjectName("slideTitle")
        sv.addWidget(self.slide_title)
        self.grid_host = QWidget()
        self.grid = QVBoxLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(self.grid_host, 1)
        mid.addWidget(self.slide, 1)
        mw = QWidget()
        mw.setLayout(mid)
        lay.addWidget(mw, 1)

        lay.addWidget(self._build_inspector())

        for sig in (bus.report_changed, bus.groups_changed, bus.data_changed):
            sig.connect(self.mark_stale)
        bus.exclusion_changed.connect(self._on_exclusion)
        self._stale = True
        self.mark_stale()

    # ── 지연 렌더 (규약은 tabs/common.StaleMixin) ────────────
    def refresh(self) -> None:
        self.rebuild()

    def _on_exclusion(self) -> None:
        """제외/복원은 점만 다시 찍으면 된다 — 슬롯 위젯을 다시 만들지 않는다.

        예전에는 클릭 한 번마다 캔버스 6개를 새로 생성했다(위젯 파괴·재생성 +
        전체 재렌더). 이제 살아 있는 캔버스에 draw_spec만 다시 준다.
        """
        if not self.isVisible():
            self.mark_stale()
            return
        if not self._canvases:
            self.rebuild()
            return
        for idx, canvas in self._canvases.items():
            spec = self._slot_spec(idx)
            if spec is not None:
                canvas.draw_spec(spec)
        self._update_info()

    def _slot_spec(self, idx: int):
        st = self.state
        if st.report is None or not (0 <= self.page_idx < len(st.report.pages)):
            return None
        return st.report.pages[self.page_idx].slots[idx]

    # ── 인스펙터 ─────────────────────────────────────────────
    def _build_inspector(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(276)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)

        self.slot_card = Card("선택한 슬롯")
        self.ed_sx = QLineEdit()
        self.ed_sy = QLineEdit()
        self.ed_stitle = QLineEdit()
        for ed in (self.ed_sx, self.ed_sy, self.ed_stitle):
            ed.editingFinished.connect(self._slot_edited)
        self.slot_card.body.addWidget(row("제목", self.ed_stitle, stretch_at=1))
        self.slot_card.body.addWidget(row("X", self.ed_sx, stretch_at=1))
        self.slot_card.body.addWidget(row("Y", self.ed_sy, stretch_at=1))
        self.cmb_log = QComboBox()
        self.cmb_log.addItems(["Y축 자동", "Y축 log", "Y축 선형"])
        self.cmb_log.currentIndexChanged.connect(self._slot_edited)
        self.slot_card.body.addWidget(self.cmb_log)
        b_del = GhostButton("이 슬롯 비우기")
        b_del.clicked.connect(self._clear_slot)
        self.slot_card.body.addWidget(b_del)
        sv.addWidget(self.slot_card)

        self.info = Card("생성될 덱")
        self.lbl_info = QLabel()
        self.lbl_info.setWordWrap(True)
        self.info.body.addWidget(self.lbl_info)
        self.info.body.addWidget(QLabel("표 슬라이드"))
        self.cmb_tbl = QComboBox()
        self.cmb_tbl.addItems(["덱 전체를 넓게", "여러 장으로 분할"])
        self.cmb_tbl.currentIndexChanged.connect(self._mode_changed)
        self.info.body.addWidget(self.cmb_tbl)
        sv.addWidget(self.info)
        sv.addStretch(1)
        return side

    def _current_slot(self):
        st = self.state
        if st.report is None or self.sel_slot is None:
            return None
        page = st.report.pages[self.page_idx]
        return page.slots[self.sel_slot]

    def _slot_edited(self) -> None:
        spec = self._current_slot()
        if spec is None:
            return
        spec.title = self.ed_stitle.text()
        spec.x = self.ed_sx.text()
        spec.y = self.ed_sy.text()
        spec.logy_mode = ("auto", "log", "linear")[self.cmb_log.currentIndex()]
        self.bus.report_changed.emit()

    def _clear_slot(self) -> None:
        st = self.state
        if st.report is None or self.sel_slot is None:
            return
        st.report.pages[self.page_idx].slots[self.sel_slot] = None
        self.sel_slot = None
        self.bus.report_changed.emit()

    def _select(self, idx: int) -> None:
        self.sel_slot = idx
        for f in self._frames:
            f.set_selected(f.index == idx)
        spec = self._current_slot()
        for ed, val in ((self.ed_stitle, spec.title if spec else ""),
                        (self.ed_sx, spec.x if spec else ""),
                        (self.ed_sy, spec.y if spec else "")):
            ed.setText(val)
            ed.setEnabled(spec is not None)
        self.cmb_log.setEnabled(spec is not None)
        if spec:
            self.cmb_log.blockSignals(True)
            self.cmb_log.setCurrentIndex(
                {"auto": 0, "log": 1, "linear": 2}.get(spec.logy_mode, 0))
            self.cmb_log.blockSignals(False)
        self.slot_card.setTitle(
            f"슬롯 {idx + 1}" if spec else f"슬롯 {idx + 1} (비어 있음)")

    # ── 드래그 재배치 ────────────────────────────────────────
    def _swap(self, a: int, b: int) -> None:
        st = self.state
        if st.report is None:
            return
        swap_slots(st.report.pages[self.page_idx].slots, a, b)
        self.sel_slot = b
        self.bus.report_changed.emit()

    # ── 점 제외 ──────────────────────────────────────────────
    def _all_toggled(self, on: bool) -> None:
        self.state.exclude_all_plots = on

    def _pick_point(self, key: str) -> None:
        from etreport.data.loader import sync_exclusion
        st = self.state
        if key in st.excluded:
            st.excluded.discard(key)
            sync_exclusion(st, key, False)
        else:
            st.excluded.add(key)
            st.undo_stack.append(key)
            sync_exclusion(st, key, True,
                           "리포트 구성에서 제외"
                           if st.exclude_all_plots else "이 plot에서만 제외")
        self.bus.exclusion_changed.emit()

    # ── 그리기 ───────────────────────────────────────────────
    def _mode_changed(self, i: int) -> None:
        self.state.table_slide_mode = "wide" if i == 0 else "split"
        self._update_info()

    def _page_changed(self, i: int) -> None:
        if i >= 0:
            self.page_idx = i
            self.sel_slot = None
            self.rebuild()

    def _title_changed(self, t: str) -> None:
        st = self.state
        if st.report and 0 <= self.page_idx < len(st.report.pages):
            st.report.pages[self.page_idx].title = t
            self.slide_title.setText(t)

    def _update_info(self) -> None:
        st = self.state
        n_exp = max(1, len(st.factors))
        n_pg = len(st.report.pages) if st.report else 0
        n_w = sum(len(w) for _, w in st.wafer_columns())
        self.lbl_info.setText(
            f"템플릿 {n_pg}페이지 × 실험 {n_exp}개 = <b>{n_pg * n_exp}페이지</b><br>"
            f"표 {len(st.report.table_names()) if st.report else 0}개 · "
            f"wafer {n_w}장<br>제외 {len(st.excluded)}점 반영")
        self.lbl_excl.setText(f"제외 {len(st.excluded)}점")

    def rebuild(self) -> None:
        st = self.state
        self.mark_fresh()
        if st.report is None or not st.report.pages:
            self.pages.clear()
            self.slide_title.setText("Plot 템플릿을 열고 REPORT를 선택하세요")
            self._clear_grid()
            return
        self.pages.blockSignals(True)
        self.pages.clear()
        for p in st.report.pages:
            n = sum(1 for s in p.slots if s)
            self.pages.addItem(f"{p.number}. {p.title}\n     슬롯 {n}/6")
        self.page_idx = min(self.page_idx, len(st.report.pages) - 1)
        self.pages.setCurrentRow(self.page_idx)
        self.pages.blockSignals(False)

        page = st.report.pages[self.page_idx]
        self.ed_title.setText(page.title)
        self.slide_title.setText(page.title)

        self._clear_grid()
        self._frames = []
        self._canvases = {}
        for r in range(2):
            hw = QWidget()
            hl = QHBoxLayout(hw)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            for c in range(3):
                idx = r * 3 + c
                spec = page.slots[idx]
                box = SlotFrame(idx, empty=spec is None)
                box.swapped.connect(self._swap)
                box.picked.connect(self._select)
                if spec is None:
                    lab = QLabel("비어 있음")
                    lab.setAlignment(Qt.AlignCenter)
                    lab.setObjectName("hint")
                    box.body.addWidget(lab)
                elif spec.type == "table":
                    cap = QLabel(spec.title)
                    cap.setObjectName("slotTitle")
                    box.body.addWidget(cap)
                    lab = QLabel("[ 표 ]")
                    lab.setAlignment(Qt.AlignCenter)
                    lab.setObjectName("hint")
                    box.body.addWidget(lab, 1)
                else:
                    cap = QLabel(spec.title)
                    cap.setObjectName("slotTitle")
                    box.body.addWidget(cap)
                    cv = PlotCanvas(st, mini=True)
                    if self.chk_pick.isChecked():
                        cv.on_pick = self._pick_point
                    cv.draw_spec(spec)
                    self._canvases[idx] = cv
                    box.body.addWidget(cv, 1)
                self._frames.append(box)
                hl.addWidget(box, 1)
            self.grid.addWidget(hw, 1)
        if self.sel_slot is not None:
            self._select(self.sel_slot)
        self._update_info()

    def _clear_grid(self) -> None:
        self._canvases = {}          # 곧 파괴될 캔버스를 붙잡고 있지 않도록
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    # ── 저장 / 생성 ──────────────────────────────────────────
    def _save_template(self) -> None:
        st = self.state
        if st.templates is None or st.report is None:
            QMessageBox.information(self, "템플릿에 저장",
                                    "plot 템플릿을 먼저 여세요")
            return
        from etreport.export.template_writer import save_to_template, summary
        if QMessageBox.question(
                self, "템플릿에 저장",
                f"현재 화면 배치를 plot 템플릿 엑셀에 씁니다.\n\n"
                f"{summary(st.report)}\n"
                f"다른 Report 행은 그대로 두고, 원본은 .bak으로 백업합니다.\n\n"
                f"계속할까요?") != QMessageBox.Yes:
            return
        try:
            bak = save_to_template(st.templates, st.report)
        except ImportError:
            QMessageBox.warning(self, "템플릿에 저장",
                                "xlwings/Excel이 없는 환경입니다 — 사내 PC에서 실행하세요")
            return
        except Exception as e:                       # noqa: BLE001
            QMessageBox.critical(self, "템플릿 저장 실패", str(e))
            return
        QMessageBox.information(self, "저장됨",
                                f"plot 템플릿에 반영했습니다.\n백업: {bak}")

    def _ppt(self) -> None:
        st = self.state
        if st.report is None or st.data is None:
            QMessageBox.warning(self, "PPT 생성", "데이터와 템플릿이 먼저 필요합니다")
            return
        out, _ = QFileDialog.getSaveFileName(
            self, "PPT 저장", f"{st.report.report}_report.pptx",
            "PowerPoint (*.pptx)")
        if not out:
            return
        from etreport.export.deckbuild import generate
        # 페이지 × 실험 × plot 6개를 전부 렌더하므로 수 분이 걸릴 수 있다.
        run_in_background(
            self, "PPT 생성", lambda: generate(st, out),
            done=lambda path: QMessageBox.information(
                self, "완료", f"저장됨:\n{path}"))
