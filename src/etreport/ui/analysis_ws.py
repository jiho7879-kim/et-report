"""분석 워크스페이스 — 도크(설정·실험·그룹·제외) + 3개 탭.

탭 자체는 ui/tabs/ 에 있다(탐색·Summary·리포트 구성). 여기서는 도크에서
파일·설정을 모아 [적용] 한 번으로 검증하고, 그 결과를 StateBus로 알린다.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from etreport.config.settings import AnalysisConfig, Settings
from etreport.model.state import AppState, StateBus
from etreport.ui.tabs.common import on_combo
from etreport.ui.tabs.common import pick_sheet as _pick_sheet
from etreport.ui.tabs.explore import ExploreTab
from etreport.ui.tabs.report import ReportTab
from etreport.ui.tabs.summary import SummaryTab
from etreport.ui.widgets.cards import GhostButton, SectionLabel
from etreport.ui.widgets.group_dialog import GroupDialog
from etreport.ui.widgets.metrology_dialog import MetrologyDialog
from etreport.ui.widgets.reformatter_dialog import ReformatterDialog
from etreport.ui.widgets.split_dialog import SplitDialog, SplitSourceDialog
from etreport.ui.widgets.sql_dialog import SqlExportDialog

__all__ = ["AnalysisWorkspace", "ExploreTab", "ReportTab", "SummaryTab",
           "_pick_sheet"]


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
        on_combo(self.cfg_combo, lambda: self._cfg_selected(
            self.cfg_combo.currentIndex()))
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

        # REPORT는 **콤보를 두지 않는다**(확정 사양 §5.1). 템플릿에 리포트가
        # 여럿이면 [적용] 뒤 안내 문구로만 알린다.
        self.lbl_report = QLabel()
        self.lbl_report.setObjectName("hint")
        self.lbl_report.setWordWrap(True)
        v.addWidget(self.lbl_report)

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

        b = GhostButton("S3 저장소")
        b.setToolTip("사내 S3에 duckdb·csv·sbdf를 올리고 내려받습니다.")
        b.clicked.connect(self._open_s3)
        v.addWidget(b)

        b = GhostButton("inline 계측 불러오기")
        b.setToolTip("fab.f_fab_wf_met에서 계측값을 가져와 (lot, wafer)로 붙입니다.\n"
                     "붙인 값은 탐색 X축·Summary에서 쓰고, 유의 인자 top-k는\n"
                     "PPT 슬라이드로 나갑니다.")
        b.clicked.connect(self._open_metrology)
        v.addWidget(b)

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
        self._show_report(c.report)
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
        # report는 [적용]이 템플릿에서 정한 값을 그대로 쓴다(콤보 없음)
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
        """엑셀 / CSV·TSV / 클립보드 붙여넣기 — 한 창에서 고른다(§3.4)."""
        c = self.cfg()
        dlg = SplitSourceDialog(self, path=c.split_path,
                                text=getattr(c, "split_text", ""))
        if dlg.exec() and dlg.matrix is not None:
            c.split_path, c.split_text = dlg.path, dlg.text
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
        """[적용]은 워커 스레드로 — Excel·DuckDB 읽기가 수 초~수십 초다.

        UI 갱신은 **완료 콜백에서만** 한다(Qt 위젯은 워커 스레드에서 만지면 안
        된다). 진행 중에는 도크를 잠가 중복 [적용]을 막는다.
        """
        from etreport.model.session import apply_config
        from etreport.ui.widgets.worker import run_in_background
        c = self.cfg()
        self._collect_into(c)
        self.btn_apply.setEnabled(False)
        self.btn_apply.setText("읽는 중…")
        self.setEnabled(False)                 # 재진입·중복 적용 방지
        w = run_in_background(
            self, "설정 적용", lambda: apply_config(self.state, c),
            done=lambda rep: self._apply_done(c, rep), needs_com=True)
        # 실패해도 잠금은 풀려야 한다 — done은 성공했을 때만 불린다
        w.finished.connect(self._apply_unlock)

    def _apply_unlock(self) -> None:
        self.setEnabled(True)
        self.btn_apply.setEnabled(True)
        self.btn_apply.setText("적용")

    def _apply_done(self, c, rep) -> None:
        """워커가 끝난 뒤 UI 반영 — 여기서만 위젯을 만진다."""
        self._apply_unlock()
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

        self._show_report(c.report)

        # split 배정은 loader.load_state가 이미 했다(그 뒤에 manual_groups까지
        # 재적용). 여기서 _apply_split을 또 부르면 그룹 편집에서 만든 그룹과
        # 손배정이 통째로 덮어써진다 — [적용] 후 그룹이 초기화되던 원인.
        self.settings.save()
        self.bus.data_changed.emit()
        self.bus.report_changed.emit()
        self._refresh_dock()

        if rep.warnings:
            QMessageBox.information(self, "적용 완료 — 일부 제외", rep.text())

    def _show_report(self, name: str) -> None:
        """적용된 리포트 이름을 문구로만 보여 준다 — 고르는 콤보는 없다(§5.1).

        한 파일에 리포트가 여럿이면 그 사실만 알린다. 어느 것을 쓸지는 템플릿의
        Report 컬럼이 정한다.
        """
        others = [r for r in self.state.reports if r != name]
        if not name:
            self.lbl_report.setText("")
            return
        text = f"REPORT  {name}"
        if others:
            text += (f"\n템플릿에 리포트 {len(self.state.reports)}개 "
                     f"({', '.join(others[:3])}{'…' if len(others) > 3 else ''}) — "
                     f"다른 리포트를 쓰려면 템플릿의 Report 열을 바꾸세요")
        self.lbl_report.setText(text)

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
        src = ("붙여넣은 내용" if getattr(c, "split_text", "")
               else short(c.split_path))
        self.btn_split.setText(f"실험 조건   {src}")

        self.lbl_factor.setText(
            f"factor · {', '.join(st.factors) or '(없음)'}" if st.split
            else "실험 조건 파일 미연결")
        cf = st.split.confounds(st.factors) if st.split else []
        self.lbl_confound.setVisible(bool(cf))
        if cf:
            self.lbl_confound.setText(
                f"⚠ 혼입 {len(cf)}건 — '{cf[0].group}' 안에 "
                f"{cf[0].step}가 {len(cf[0].codes)}종 섞여 있습니다")

        # 그룹별 포인트 수 — group_by 한 번으로 (제외를 찍을 때마다 불리는 자리라
        # 그룹마다 전체 프레임을 필터하면 데이터가 커질수록 클릭이 무거워진다)
        counts: dict[str, int] = {}
        if st.data is not None and st.groups:
            counts = {r["gid"]: r["len"] for r in
                      st.active().group_by("gid").len().iter_rows(named=True)}

        self.group_list.blockSignals(True)
        self.group_list.clear()
        for g in st.groups:
            n = counts.get(g.gid, 0)
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
        """factor 편집 결과를 반영한다. **[적용] 경로에서는 부르지 않는다** —
        거기서는 loader.load_state가 같은 일을 이미 순서대로 끝낸다.

        순서는 loader와 같다: split 배정 → manual 배정(사용자가 고른 쪽이 이긴다).
        손으로 만든 그룹의 이름·색은 gid가 겹치면 보존한다.
        """
        from etreport.data.loader import apply_manual_groups
        st = self.state
        if st.split is None:
            return
        keep = {g.gid: g for g in st.groups}
        fresh = st.split.styles_for(st.factors)
        for g in fresh:                     # 같은 gid면 사용자가 정한 이름·색 유지
            old = keep.pop(g.gid, None)
            if old is not None:
                g.name, g.color, g.symbol, g.size = (old.name, old.color,
                                                     old.symbol, old.size)
        # split이 만들지 않은 그룹(손으로 추가한 것)은 뒤에 남긴다
        used = set(st.manual_groups.values())
        st.groups = fresh + [g for g in keep.values() if g.gid in used]
        if st.data is not None:
            assign = st.split.assignment(st.factors)
            st.data = st.data.with_columns(pl.Series(
                "gid", [assign.get((lo, wa), "") for lo, wa
                        in zip(st.data["lot"], st.data["wafer"])]))
            st.data = apply_manual_groups(st.data, st.manual_groups)
        if not silent:
            self.bus.groups_changed.emit()

    def _edit_groups(self) -> None:
        # DB를 고르기만 하고 [적용] 전에 그룹부터 짜는 흐름을 위해 경로를 넘긴다
        if GroupDialog(self.state, self, db_path=self.cfg().db_path).exec():
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

    def _open_s3(self) -> None:
        """S3 창(기능 C) — boto3가 없는 PC에서는 안내만 남긴다."""
        try:
            from etreport.ui.widgets.s3_dialog import S3Dialog
        except ImportError as e:               # 창 자체를 못 만드는 경우
            QMessageBox.information(self, "S3", f"S3 기능을 쓸 수 없습니다: {e}")
            return
        S3Dialog(self, default_dir=str(Path(self.cfg().db_path).parent
                                       if self.cfg().db_path else "")).exec()

    def _open_metrology(self) -> None:
        """inline 계측 창(기능 B) — 붙인 뒤에는 다시 그려야 하므로 알린다."""
        dlg = MetrologyDialog(self.state, self)
        dlg.exec()
        if self.state.met_columns:
            self.bus.data_changed.emit()
            self._refresh_dock()

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
