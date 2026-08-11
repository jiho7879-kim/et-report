"""분석 워크스페이스 — 도크(설정·실험·그룹·제외) + 3개 탭."""
from __future__ import annotations

import copy
from pathlib import Path

import polars as pl
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from etreport.config.settings import AnalysisConfig, Settings
from etreport.model.specs import fmt_value
from etreport.model.state import AppState, StateBus
from etreport.ui.widgets.autocomplete import AutoCompleteEdit
from etreport.ui.widgets.cards import Card, GhostButton, SectionLabel, row
from etreport.ui.widgets.group_dialog import GroupDialog
from etreport.ui.widgets.plot_canvas import PlotCanvas
from etreport.ui.widgets.reformatter_dialog import ReformatterDialog
from etreport.ui.widgets.slot_grid import SlotFrame, swap_slots
from etreport.ui.widgets.split_dialog import SplitDialog
from etreport.ui.widgets.sql_dialog import SqlExportDialog


def _pick_sheet(parent, path: str, what: str) -> str | int | None:
    """시트가 여럿이면 고르게 한다. 하나면 그대로, 취소하면 None.

    시트 목록 조회도 xlwings라 Excel 없는 환경에서는 첫 시트로 폴백.
    """
    try:
        from etreport.data.xlio import sheet_names
        names = sheet_names(path)
    except ImportError:
        return 0
    except Exception as e:                           # noqa: BLE001
        QMessageBox.critical(parent, what, f"파일을 열 수 없습니다:\n{e}")
        return None
    if len(names) <= 1:
        return names[0] if names else 0
    from PySide6.QtWidgets import QInputDialog
    name, ok = QInputDialog.getItem(
        parent, what, f"시트를 선택하세요 ({len(names)}개)", names, 0, False)
    return name if ok else None


class AnalysisWorkspace(QWidget):
    def __init__(self, state: AppState, bus: StateBus,
                 settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.state, self.bus, self.settings = state, bus, settings
        self._applied = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_dock())

        self.tabs = QTabWidget()
        self.tab_explore = ExploreTab(state, bus)
        self.tab_summary = SummaryTab(state, bus)
        self.tab_report = ReportTab(state, bus)
        self.tabs.addTab(self.tab_explore, "탐색")
        self.tabs.addTab(self.tab_summary, "Summary")
        self.tabs.addTab(self.tab_report, "리포트 구성")
        lay.addWidget(self.tabs, 1)

        for sig in (bus.groups_changed, bus.exclusion_changed, bus.data_changed):
            sig.connect(self._refresh_dock)
        self._refresh_dock()

    # ── 도크 ─────────────────────────────────────────────────
    def _build_dock(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("dock")
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        # 설정 프리셋 -----------------------------------------
        v.addWidget(SectionLabel("설정"))
        self.cfg_combo = QComboBox()
        self.cfg_combo.currentIndexChanged.connect(self._cfg_selected)
        v.addWidget(self.cfg_combo)
        btns = QHBoxLayout()
        for text, fn in (("저장", self._cfg_save),
                         ("새 이름", self._cfg_save_as),
                         ("삭제", self._cfg_delete)):
            b = GhostButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        v.addLayout(btns)

        # 파일 4행 (고르면 경로만 담아 둔다) --------------------
        for key, _label, fn in (
                ("db", "DB", self._pick_db),
                ("plot", "Plot", lambda: self._pick_tpl("plot")),
                ("tbl", "Table", lambda: self._pick_tpl("table")),
                ("rfm", "리포메터", self._pick_rfm),
                ("split", "실험 조건", self._pick_split)):
            b = GhostButton("")
            b.setObjectName("cfgRow")
            b.clicked.connect(fn)
            setattr(self, f"btn_{key}", b)
            v.addWidget(b)

        v.addWidget(QLabel("REPORT"))
        self.rep_combo = QComboBox()
        self.rep_combo.currentTextChanged.connect(self._report_changed)
        v.addWidget(self.rep_combo)

        self.btn_apply = QPushButton("적용")
        self.btn_apply.setToolTip(
            "고른 파일들을 한 번에 읽고 검증합니다.\n"
            "파일을 고르는 동안에는 Excel을 열지 않습니다.")
        self.btn_apply.clicked.connect(self.apply_config)
        v.addWidget(self.btn_apply)
        self.lbl_apply = QLabel("파일을 고르고 [적용]을 누르세요")
        self.lbl_apply.setObjectName("hint")
        self.lbl_apply.setWordWrap(True)
        v.addWidget(self.lbl_apply)

        b = GhostButton("SQL 조회 · 내보내기")
        b.clicked.connect(self._open_sql)
        v.addWidget(b)
        self.btn_cache = GhostButton("")
        self.btn_cache.setToolTip(
            "Excel 읽기 결과를 로컬에 캐시합니다.\n"
            "누르면 캐시를 비우고 다음에 Excel에서 새로 읽습니다.")
        self.btn_cache.clicked.connect(self._clear_cache)
        v.addWidget(self.btn_cache)

        # plot -------------------------------------------------
        v.addWidget(SectionLabel("plot"))
        v.addWidget(QLabel("로그 축 item 패턴"))
        self.ed_log = QLineEdit(", ".join(self.state.log_patterns))
        self.ed_log.editingFinished.connect(self._log_changed)
        v.addWidget(self.ed_log)

        # 실험 조건 --------------------------------------------
        v.addWidget(SectionLabel("실험 조건"))
        b = GhostButton("factor 편집")
        b.clicked.connect(self._edit_split)
        v.addWidget(b)
        self.lbl_factor = QLabel()
        self.lbl_factor.setObjectName("hint")
        self.lbl_factor.setWordWrap(True)
        v.addWidget(self.lbl_factor)
        self.lbl_confound = QLabel()
        self.lbl_confound.setObjectName("warn")
        self.lbl_confound.setWordWrap(True)
        v.addWidget(self.lbl_confound)

        # 그룹 -------------------------------------------------
        v.addWidget(SectionLabel("그룹"))
        self.group_list = QListWidget()
        self.group_list.setObjectName("groupList")
        self.group_list.setFixedHeight(120)
        self.group_list.itemChanged.connect(self._toggle_group)
        v.addWidget(self.group_list)
        b = GhostButton("그룹 편집")
        b.clicked.connect(self._edit_groups)
        v.addWidget(b)

        # 제외 -------------------------------------------------
        v.addWidget(SectionLabel("제외 포인트"))
        self.lbl_excl = QLabel("0")
        self.lbl_excl.setObjectName("bigNum")
        self.lbl_excl.setAlignment(Qt.AlignCenter)
        v.addWidget(self.lbl_excl)
        self.btn_undo = GhostButton("되돌리기")
        self.btn_undo.clicked.connect(self._undo)
        v.addWidget(self.btn_undo)
        v.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setFixedWidth(276)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("dockScroll")
        self._fill_cfg_combo()
        return scroll

    # ── 설정 프리셋 ──────────────────────────────────────────
    def _fill_cfg_combo(self) -> None:
        self.cfg_combo.blockSignals(True)
        self.cfg_combo.clear()
        self.cfg_combo.addItems([c.name for c in self.settings.analysis_configs])
        idx = next((i for i, c in enumerate(self.settings.analysis_configs)
                    if c.name == self.settings.last_analysis_config), 0)
        self.cfg_combo.setCurrentIndex(idx)
        self.cfg_combo.blockSignals(False)
        self._cfg_selected(idx)

    def cfg(self) -> AnalysisConfig:
        i = max(0, self.cfg_combo.currentIndex())
        if not self.settings.analysis_configs:
            self.settings.analysis_configs.append(AnalysisConfig(name="기본"))
        return self.settings.analysis_configs[
            min(i, len(self.settings.analysis_configs) - 1)]

    def _cfg_selected(self, _i: int) -> None:
        c = self.cfg()
        self.settings.last_analysis_config = c.name
        self.ed_log.setText(", ".join(c.log_patterns))
        self.rep_combo.blockSignals(True)
        self.rep_combo.clear()
        if c.report:
            self.rep_combo.addItem(c.report)
        self.rep_combo.blockSignals(False)
        self._mark_unapplied("설정을 불러왔습니다 — [적용]을 누르세요")
        self._refresh_dock()

    def _cfg_save(self) -> None:
        self._collect_into(self.cfg())
        self.settings.save()
        self.lbl_apply.setText(f"'{self.cfg().name}' 저장됨")

    def _cfg_save_as(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "새 설정", "이름")
        if not (ok and name.strip()):
            return
        import copy
        c = copy.deepcopy(self.cfg())
        c.name = name.strip()
        self._collect_into(c)
        self.settings.analysis_configs.append(c)
        self.settings.save()
        self.cfg_combo.addItem(c.name)
        self.cfg_combo.setCurrentIndex(self.cfg_combo.count() - 1)

    def _cfg_delete(self) -> None:
        if len(self.settings.analysis_configs) <= 1:
            QMessageBox.information(self, "삭제", "설정이 하나뿐입니다")
            return
        name = self.cfg().name
        if QMessageBox.question(self, "삭제", f"'{name}' 설정을 지울까요?") \
                != QMessageBox.Yes:
            return
        self.settings.analysis_configs.remove(self.cfg())
        self.settings.save()
        self._fill_cfg_combo()

    def _collect_into(self, c: AnalysisConfig) -> None:
        c.log_patterns = [t.strip() for t in self.ed_log.text().split(",")
                          if t.strip()]
        if self.rep_combo.currentText():
            c.report = self.rep_combo.currentText()
        c.table_slide_mode = self.state.table_slide_mode

    # ── 파일 고르기 (읽지 않는다) ────────────────────────────
    def _pick_db(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "DuckDB 파일", "",
                                           "DuckDB (*.duckdb)")
        if p:
            self.cfg().db_path = p
            self._mark_unapplied()

    def _pick_tpl(self, kind: str) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self, f"{kind} 템플릿", "", "Excel (*.xlsx *.xlsm)")
        if not p:
            return
        sheet = _pick_sheet(self, p, f"{kind} 템플릿")
        if sheet is None:
            return
        c = self.cfg()
        if kind == "plot":
            c.plot_template_path = p
            c.plot_sheet = sheet if isinstance(sheet, str) else ""
        else:
            c.table_template_path = p
            c.table_sheet = sheet if isinstance(sheet, str) else ""
        self._mark_unapplied()

    def _pick_rfm(self) -> None:
        """리포메터 창 — 시트를 바꿔 가며 확인하고 적용."""
        dlg = ReformatterDialog(self.state, self)
        if not dlg.exec():
            return
        rf = dlg.result_reformatter()
        if rf is None:
            return
        self.state.rf = rf
        self.state.rf_path = dlg.path
        self.state.rf_sheet = dlg.sheet
        c = self.cfg()
        c.reformatter_path = dlg.path
        c.reformatter_sheet = dlg.sheet if isinstance(dlg.sheet, str) else ""
        self._mark_unapplied("리포메터가 바뀌었습니다 — [적용]으로 템플릿을 다시 맞추세요")

    def _pick_split(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self, "실험 조건 파일", "",
            "표 파일 (*.xlsx *.xlsm *.csv *.tsv *.txt)")
        if p:
            self.cfg().split_path = p
            self._mark_unapplied()

    # ── 적용 (검증은 여기서 한 번) ───────────────────────────
    def _mark_unapplied(self, msg: str = "변경됨 — [적용]을 누르세요") -> None:
        self._applied = False
        self.btn_apply.setProperty("dirty", "true")
        self.btn_apply.style().unpolish(self.btn_apply)
        self.btn_apply.style().polish(self.btn_apply)
        self.lbl_apply.setText(msg)
        self._refresh_dock()

    def apply_config(self) -> None:
        from etreport.model.session import apply_config
        c = self.cfg()
        self._collect_into(c)
        self.btn_apply.setEnabled(False)
        self.btn_apply.setText("읽는 중…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rep = apply_config(self.state, c)
        finally:
            QApplication.restoreOverrideCursor()
            self.btn_apply.setEnabled(True)
            self.btn_apply.setText("적용")

        if not rep.ok:
            self.lbl_apply.setText("적용 실패")
            QMessageBox.critical(self, "적용 실패", rep.text())
            return

        self._applied = True
        self.btn_apply.setProperty("dirty", "false")
        self.btn_apply.style().unpolish(self.btn_apply)
        self.btn_apply.style().polish(self.btn_apply)
        self.lbl_apply.setText(
            f"적용됨 · {rep.elapsed:.1f}초"
            + (f" · 제외 {len(rep.warnings)}건" if rep.warnings else ""))

        self.rep_combo.blockSignals(True)
        self.rep_combo.clear()
        self.rep_combo.addItems(self.state.reports)
        if c.report in self.state.reports:
            self.rep_combo.setCurrentText(c.report)
        self.rep_combo.blockSignals(False)

        if self.state.split is not None and self.state.factors:
            self._apply_split(silent=True)
        self.settings.save()
        self.bus.data_changed.emit()
        self.bus.report_changed.emit()
        self._refresh_dock()

        if rep.warnings:
            QMessageBox.information(self, "적용 완료 — 일부 제외", rep.text())

    def _report_changed(self, name: str) -> None:
        st = self.state
        if not name or st.templates is None:
            return
        from etreport.model.templates import build_report
        st.report = build_report(st.templates, name)
        self.cfg().report = name
        self.bus.report_changed.emit()

    # ── 표시 갱신 ────────────────────────────────────────────
    def _refresh_dock(self) -> None:
        st, c = self.state, self.cfg()

        def short(p: str) -> str:
            return Path(p).name if p else "(선택 안 됨)"

        self.btn_db.setText(f"DB          {short(c.db_path)}"
                            + (f"  [{st.table}]" if st.table else ""))
        self.btn_plot.setText(f"Plot        {short(c.plot_template_path)}"
                              + (f"  [{c.plot_sheet}]" if c.plot_sheet else ""))
        self.btn_tbl.setText(f"Table       {short(c.table_template_path)}"
                             + (f"  [{c.table_sheet}]" if c.table_sheet else ""))
        self.btn_rfm.setText(
            f"리포메터    {short(c.reformatter_path)}"
            + (f"  [{c.reformatter_sheet}]" if c.reformatter_sheet else ""))
        self.btn_split.setText(f"실험 조건   {short(c.split_path)}")

        self.lbl_factor.setText(
            f"factor · {', '.join(st.factors) or '(없음)'}" if st.split
            else "실험 조건 파일 미연결")
        cf = st.split.confounds(st.factors) if st.split else []
        self.lbl_confound.setVisible(bool(cf))
        if cf:
            self.lbl_confound.setText(
                f"⚠ 혼입 {len(cf)}건 — '{cf[0].group}' 안에 "
                f"{cf[0].step}가 {len(cf[0].codes)}종 섞여 있습니다")

        self.group_list.blockSignals(True)
        self.group_list.clear()
        for g in st.groups:
            n = 0 if st.data is None else st.active().filter(
                pl.col("gid") == g.gid).height
            it = QListWidgetItem(f"  {g.name}{'  · REF' if g.ref else ''}   {n}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if g.visible else Qt.Unchecked)
            it.setForeground(QColor(g.color))
            it.setData(Qt.UserRole, g.gid)
            self.group_list.addItem(it)
        self.group_list.blockSignals(False)

        try:
            from etreport.data.xlio import cache_stats
            n, size = cache_stats()
            self.btn_cache.setText(
                f"Excel 캐시   {n}개 · {size / 1024:.0f} KB" if n
                else "Excel 캐시   비어 있음")
        except Exception:                            # noqa: BLE001
            self.btn_cache.setText("Excel 캐시")

        self.lbl_excl.setText(str(len(st.excluded)))
        self.lbl_excl.setProperty("zero", "true" if not st.excluded else "false")
        self.lbl_excl.style().unpolish(self.lbl_excl)
        self.lbl_excl.style().polish(self.lbl_excl)
        self.btn_undo.setEnabled(bool(st.undo_stack))

    # ── 나머지 동작 ──────────────────────────────────────────
    def _toggle_group(self, item: QListWidgetItem) -> None:
        g = self.state.group(item.data(Qt.UserRole))
        if g:
            g.visible = item.checkState() == Qt.Checked
            self.bus.groups_changed.emit()

    def _edit_split(self) -> None:
        if self.state.split is None:
            QMessageBox.information(
                self, "실험 조건",
                "실험 조건 파일을 고르고 [적용]을 누른 뒤 편집할 수 있습니다")
            return
        if SplitDialog(self.state, self).exec():
            self._apply_split()

    def _apply_split(self, silent: bool = False) -> None:
        st = self.state
        if st.split is None:
            return
        st.groups = st.split.styles_for(st.factors)
        if st.data is not None:
            assign = st.split.assignment(st.factors)
            st.data = st.data.with_columns(pl.Series(
                "gid", [assign.get((lo, wa), "") for lo, wa
                        in zip(st.data["lot"], st.data["wafer"])]))
        if not silent:
            self.bus.groups_changed.emit()

    def _edit_groups(self) -> None:
        if GroupDialog(self.state, self).exec():
            self.bus.groups_changed.emit()

    def _undo(self) -> None:
        from etreport.data.loader import sync_exclusion
        if self.state.undo_stack:
            key = self.state.undo_stack.pop()
            self.state.excluded.discard(key)
            sync_exclusion(self.state, key, False)
            self.bus.exclusion_changed.emit()

    def _clear_cache(self) -> None:
        from etreport.data import xlio
        n = xlio.invalidate()
        self._refresh_dock()
        QMessageBox.information(self, "Excel 캐시",
                                f"{n}개 캐시를 비웠습니다 — 다음 읽기는 Excel을 엽니다")

    def _open_sql(self) -> None:
        st = self.state
        path = st.db_path or self.cfg().db_path
        if not path:
            QMessageBox.information(self, "SQL 조회", "먼저 DB 파일을 고르세요")
            return
        SqlExportDialog(path, st.table or "et_data", self).exec()

    def _log_changed(self) -> None:
        self.state.log_patterns = [t.strip() for t in self.ed_log.text().split(",")
                                   if t.strip()]
        self.cfg().log_patterns = self.state.log_patterns
        self.bus.explore_changed.emit()
        self.bus.report_changed.emit()

    def connect_db(self, path: str, table: str | None = None) -> None:
        """데이터 워크스페이스 적재 직후 호출 — 그 DB로 갈아끼운다."""
        self.cfg().db_path = path
        self.apply_config()


# ═════════════════════ 탐색 ═════════════════════
class ExploreTab(QWidget):
    def __init__(self, state: AppState, bus: StateBus, parent=None) -> None:
        super().__init__(parent)
        self.state, self.bus = state, bus
        lay = QHBoxLayout(self)

        left = QVBoxLayout()
        bar = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["클릭 → 제외", "클릭 → 복원"])
        bar.addWidget(self.mode)
        b = GhostButton("＋ 리포트에 추가")
        b.clicked.connect(self._add_to_report)
        bar.addWidget(b)
        self.btn_draw = QPushButton("그리기")
        self.btn_draw.clicked.connect(self.redraw)
        bar.addWidget(self.btn_draw)
        bar.addStretch(1)
        self.lbl_info = QLabel()
        self.lbl_info.setObjectName("hint")
        bar.addWidget(self.lbl_info)
        left.addLayout(bar)
        self._stale = True

        self.canvas = PlotCanvas(state)
        self.canvas.on_pick = self._pick
        left.addWidget(self.canvas, 1)
        lw = QWidget()
        lw.setLayout(left)
        lay.addWidget(lw, 1)

        side = QVBoxLayout()
        card = Card("축")
        def _items() -> list[str]:
            if state.rf.rules:
                return state.aliases()
            if state.data is not None:
                return [c for c in state.data.columns
                        if c not in ("key", "lot", "wafer", "gid")]
            return []
        self.ed_x = AutoCompleteEdit(_items)
        self.ed_y = AutoCompleteEdit(_items)
        self.ed_x.setText(state.explore.x)
        self.ed_y.setText(state.explore.y)
        self.ed_x.editingFinished.connect(self._axes_changed)
        self.ed_y.editingFinished.connect(self._axes_changed)
        card.body.addWidget(row("X", self.ed_x, stretch_at=1))
        card.body.addWidget(row("Y", self.ed_y, stretch_at=1))
        hint = QLabel("쉼표로 여러 xy쌍 → 한 그림에 겹칩니다\n"
                      "축 범위 자동 · 규격 ∪ 데이터 × 1.2\n"
                      "로그는 item 이름 규칙으로 자동")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setMinimumHeight(56)
        card.body.addWidget(hint)
        card.body.addSpacing(4)
        self.chk_ref = QCheckBox("REF μ±3σ 밴드")
        self.chk_ref.setChecked(True)
        self.chk_ref.toggled.connect(self._axes_changed)
        card.body.addWidget(self.chk_ref)
        side.addWidget(card)
        side.addStretch(1)
        sw = QWidget()
        sw.setFixedWidth(276)
        sw.setLayout(side)
        lay.addWidget(sw)

        for sig in (bus.groups_changed, bus.data_changed, bus.explore_changed):
            sig.connect(self.mark_stale)
        bus.exclusion_changed.connect(self._on_exclusion)
        self.mark_stale()

    # ── 지연 계산 ────────────────────────────────────────────
    def mark_stale(self) -> None:
        self._stale = True
        self.btn_draw.setProperty("dirty", "true")
        self.btn_draw.style().unpolish(self.btn_draw)
        self.btn_draw.style().polish(self.btn_draw)
        self.lbl_info.setText("변경됨 — [그리기]")
        if self.isVisible():
            self.redraw()

    def _on_exclusion(self) -> None:
        # 이 화면에서 찍은 제외는 바로 반영, 아니면 표시만
        if self.isVisible():
            self.redraw()
        else:
            self.mark_stale()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self._stale:
            self.redraw()

    def _axes_changed(self) -> None:
        self.state.explore.x = self.ed_x.text()
        self.state.explore.y = self.ed_y.text()
        self.state.explore.ref_band = self.chk_ref.isChecked()
        self.redraw()

    def _pick(self, key: str) -> None:
        from etreport.data.loader import sync_exclusion
        st = self.state
        if self.mode.currentIndex() == 0:
            if key not in st.excluded:
                st.excluded.add(key)
                st.undo_stack.append(key)
                sync_exclusion(st, key, True)
        else:
            st.excluded.discard(key)
            sync_exclusion(st, key, False)
        self.bus.exclusion_changed.emit()

    def _add_to_report(self) -> None:
        st = self.state
        if st.report is None:
            return
        for page in st.report.pages:
            for i, s in enumerate(page.slots):
                if s is None:
                    spec = copy.deepcopy(st.explore)
                    spec.title = f"{st.explore.y} vs {st.explore.x}"
                    page.slots[i] = spec
                    self.bus.report_changed.emit()
                    QMessageBox.information(
                        self, "추가됨", f"Page {page.number} 슬롯 {i + 1}에 넣었습니다")
                    return
        QMessageBox.information(self, "가득 참", "빈 슬롯이 없습니다")

    def redraw(self) -> None:
        st = self.state
        self._stale = False
        self.btn_draw.setProperty("dirty", "false")
        self.btn_draw.style().unpolish(self.btn_draw)
        self.btn_draw.style().polish(self.btn_draw)
        if st.data is None:
            self.lbl_info.setText(
                "데이터 없음 — [데이터]에서 추출·적재하거나 도크에서 DB를 여세요")
            self.canvas.figure.clear()
            self.canvas.draw_idle()
            return
        if not st.explore.x and st.data.width > 4:
            items = [c for c in st.data.columns
                     if c not in ("key", "lot", "wafer", "gid")]
            st.explore.x, st.explore.y = items[0], items[min(1, len(items) - 1)]
        for ed, val in ((self.ed_x, st.explore.x), (self.ed_y, st.explore.y)):
            if not ed.hasFocus() and ed.text() != val:
                ed.setText(val)
        self.canvas.draw_spec(st.explore)
        n = st.data.height
        self.lbl_info.setText(f"{n - len(st.excluded):,} / {n:,} 포인트")


ROW_H = 26


def _fit_table(t: QTableWidget) -> None:
    """표를 내용 크기에 딱 맞춰 — 잘림·회색 여백·스크롤바가 남지 않게."""
    t.resizeColumnsToContents()
    for c in range(t.columnCount()):
        t.setColumnWidth(c, t.columnWidth(c) + 12)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
    t.verticalHeader().setDefaultSectionSize(ROW_H)
    for r in range(t.rowCount()):
        t.setRowHeight(r, ROW_H)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    hh = max(t.horizontalHeader().sizeHint().height(), 34)
    t.setMaximumWidth(sum(t.columnWidth(c)
                          for c in range(t.columnCount())) + 4)
    t.setFixedHeight(hh + ROW_H * t.rowCount() + 6)


# ═════════════════════ Summary ═════════════════════
class SummaryTab(QWidget):
    """표는 [표 만들기]를 눌렀을 때만 계산한다 (자동 재계산 없음)."""

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

    # ── 지연 계산 ────────────────────────────────────────────
    def mark_stale(self) -> None:
        self._stale = True
        self.btn_build.setProperty("dirty", "true")
        self.btn_build.style().unpolish(self.btn_build)
        self.btn_build.style().polish(self.btn_build)
        self.lbl_state.setText("변경됨 — [표 만들기]를 누르세요")

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
            _fit_table(t)
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

        self._stale = False
        self.btn_build.setProperty("dirty", "false")
        self.btn_build.style().unpolish(self.btn_build)
        self.btn_build.style().polish(self.btn_build)
        self.lbl_state.setText(
            f"{len(st.report.table_names())}개 표 · {time.monotonic() - t0:.2f}초")

    def _copy(self, cat1: str) -> None:
        st = self.state
        ws, _ref, _agg = self._stats()
        header = st.wafer_columns()
        lines = ["\t".join(["", "", ""] + [lo for lo, wl in header for _ in wl]),
                 "\t".join(["CAT2", "CAT3", "item"] +
                            [w for _, wl in header for w in wl])]
        for rs in (r for r in st.report.table_rows if r.cat1 == cat1):
            vals = [fmt_value(ws.get(rs.item_id, lot, wf))
                    for lot, wl in header for wf in wl]
            lines.append("\t".join([rs.cat2, rs.cat3, rs.item_id, *vals]))
        QApplication.clipboard().setText("\n".join(lines))
        QMessageBox.information(self, "복사됨", f"{cat1} 표를 클립보드에 복사했습니다")

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
        from etreport.export.excel import SummaryOptions, build_table, export_xlsx
        opt = SummaryOptions(
            agg="avg" if self.agg.currentIndex() == 0 else "std",
            delta_vs_ref=self.chk_delta.isChecked(),
            session_caption=f"{st.report.report} · 제외 {len(st.excluded)}점 반영")
        try:
            export_xlsx([build_table(st, n, opt) for n in names], out, opt)
        except ImportError:
            QMessageBox.warning(self, "xlsx 내보내기",
                                "xlwings/Excel이 없는 환경입니다 — 사내 PC에서 실행하세요")
            return
        except Exception as e:                       # noqa: BLE001
            QMessageBox.critical(self, "xlsx 내보내기 실패", str(e))
            return
        QMessageBox.information(self, "완료", f"저장됨:\n{out}")


# ═════════════════════ 리포트 구성 ═════════════════════
class ReportTab(QWidget):
    """슬롯을 드래그로 재배치하고, 슬롯 안에서 바로 점을 제외할 수 있다.

    order(=슬롯 위치)를 화면에서 바꾸면 [템플릿에 저장]으로 엑셀에 되쓴다.
    """

    def __init__(self, state: AppState, bus: StateBus, parent=None) -> None:
        super().__init__(parent)
        self.state, self.bus = state, bus
        self.page_idx = 0
        self.sel_slot: int | None = None
        self._frames: list[SlotFrame] = []

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

    # ── 지연 렌더 ────────────────────────────────────────────
    def mark_stale(self) -> None:
        self._stale = True
        self.btn_draw.setProperty("dirty", "true")
        self.btn_draw.style().unpolish(self.btn_draw)
        self.btn_draw.style().polish(self.btn_draw)
        if self.isVisible():
            self.rebuild()

    def _on_exclusion(self) -> None:
        if self.isVisible():
            self.rebuild()
        else:
            self.mark_stale()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self._stale:
            self.rebuild()

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
        self._stale = False
        self.btn_draw.setProperty("dirty", "false")
        self.btn_draw.style().unpolish(self.btn_draw)
        self.btn_draw.style().polish(self.btn_draw)
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
                    box.body.addWidget(cv, 1)
                self._frames.append(box)
                hl.addWidget(box, 1)
            self.grid.addWidget(hw, 1)
        if self.sel_slot is not None:
            self._select(self.sel_slot)
        self._update_info()

    def _clear_grid(self) -> None:
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
        try:
            from etreport.export.deckbuild import generate
            path = generate(st, out)
        except Exception as e:                       # noqa: BLE001
            QMessageBox.critical(self, "PPT 생성 실패", str(e))
            return
        QMessageBox.information(self, "완료", f"저장됨:\n{path}")
