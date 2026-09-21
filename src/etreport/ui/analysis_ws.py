"""분석 워크스페이스 — 소스 레일 + 탭 3종 + 인스펙터 + 액션바를 조립한다.

네 조각의 역할 분담은 설계 §1·§2에 있고 코드도 그대로 나뉜다:
`ui/source_rail.py`(무엇을 보고 있나) · `ui/tabs/`(측정면) ·
`ui/inspector.py`(어떻게 보일까) · `ui/actionbar.py`(주 동작과 결과).

여기가 갖는 것은 **동작과 상태**다: 파일·설정을 모아 [적용] 한 번으로 읽고
검증하고(`model/session.apply_config`), 그 결과를 StateBus로 알린다. 레일은
위젯만 갖고 눌리면 여기를 부른다 — 화면 조각이 세션 일을 하지 않게.
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from etreport.config.settings import AnalysisConfig, Settings
from etreport.model import outliers
from etreport.model.state import AppState, StateBus
from etreport.ui import guidance
from etreport.ui.actionbar import ActionBar
from etreport.ui.inspector import InspectorPanel
from etreport.ui.source_rail import RAIL_WIDTH, SourceRail
from etreport.ui.tabs.common import pick_sheet as _pick_sheet
from etreport.ui.tabs.explore import ExploreTab
from etreport.ui.tabs.report import ReportTab
from etreport.ui.tabs.summary import SummaryTab
from etreport.ui.widgets.cards import ChromeSection
from etreport.ui.widgets.group_dialog import GroupDialog
from etreport.ui.widgets.group_section import GroupSection
from etreport.ui.widgets.metrology_dialog import MetrologyDialog
from etreport.ui.widgets.reformatter_dialog import ReformatterDialog
from etreport.ui.widgets.split_dialog import SplitDialog, SplitSourceDialog
from etreport.ui.widgets.sql_dialog import SqlExportDialog

__all__ = ["RAIL_WIDTH", "AnalysisWorkspace", "ExploreTab", "ReportTab",
           "SummaryTab", "_pick_sheet"]

log = logging.getLogger(__name__)

#: 레일이 갖고 있는 위젯 — 예전 이름 그대로 워크스페이스에서도 찾을 수 있게
#: 한다(설계 §8 완충). 여기 없는 이름은 평소처럼 AttributeError다.
_RAIL_WIDGETS = frozenset({
    "cfg_combo", "btn_cfg_menu", "btn_db", "btn_plot", "btn_tbl", "btn_rfm",
    "btn_split", "_file_values", "lot_section", "lot_list", "lot_search",
    "btn_coverage", "cond_section", "cond_combos", "btn_cond_clear",
    "tukey_section", "chk_tukey", "cmb_tukey_k", "cmb_tukey_scope",
    "btn_tukey_log", "sources_section", "lbl_sources", "btn_factor",
    "lbl_factor", "btn_apply", "lbl_apply", "btn_apply_log", "lbl_report", "lbl_summary",
    "btn_undo",
})


class AnalysisWorkspace(QWidget):
    def __init__(self, state: AppState, bus: StateBus,
                 settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.state, self.bus, self.settings = state, bus, settings
        self._applied = False
        self._guide = False              # 가이드 모드(F2) — 다음 한 곳만 강조

        # 3단 + 하단 액션바(설계 §2). 왼쪽 레일은 **무엇을 보고 있나**,
        # 오른쪽 인스펙터는 **그것을 어떻게 보일까**, 아래 액션바는 주 동작과
        # 결과를 꺼내는 자리다. 탭은 가운데 측정면만 갖는다.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.rail = SourceRail(self)
        body.addWidget(self.rail)

        self.tabs = QTabWidget()
        self.tab_explore = ExploreTab(state, bus)
        self.tab_summary = SummaryTab(state, bus)
        self.tab_report = ReportTab(state, bus)
        self.tabs.addTab(self.tab_explore, "탐색")
        self.tabs.addTab(self.tab_summary, "요약")
        self.tabs.addTab(self.tab_report, "리포트 구성")
        body.addWidget(self.tabs, 1)

        self.inspector = InspectorPanel()
        body.addWidget(self.inspector)
        root.addLayout(body, 1)

        self.actionbar = ActionBar()
        root.addWidget(self.actionbar)

        # 탭이 넘긴 위젯을 양쪽에 등록한다 — **탭 순서가 곧 페이지 번호**다.
        # 위젯을 새로 만들지 않고 그대로 담으므로 dirty 표시(`•`)·Ctrl+Enter·
        # 비활성 처리가 지금까지와 같은 한 벌로 남는다.
        for tab in self.tab_widgets():
            self.actionbar.add_page(tab.action_items())
            self.inspector.add_page(tab.inspector_sections())
        # 탭과 무관한 공용 섹션 — **진짜 한 인스턴스**여야 규칙 2가 지켜진다
        # (예전에는 그룹 스타일 카드가 탐색·리포트에 한 벌씩 있었다).
        self.group_section = GroupSection(state, bus, on_edit=self._edit_groups)
        self.inspector.add_shared(self.group_section)
        self.inspector.add_shared(self._build_view_section())
        self.tabs.currentChanged.connect(self._tab_changed)
        self._tab_changed(self.tabs.currentIndex())

        for sig in (bus.groups_changed, bus.exclusion_changed, bus.data_changed):
            sig.connect(self._refresh_dock)
        self._build_shortcuts()
        self._fill_cfg_combo()
        self._refresh_dock()

    def __getattr__(self, name: str):
        """레일 위젯을 예전 이름으로도 찾게 한다(설계 §8 완충).

        `_refresh_dock`처럼 위젯을 만지는 코드와 테스트가 `ws.chk_tukey`로
        쓰고 있었다. 자리를 옮겼다고 그 주소를 한꺼번에 바꾸면 무엇이 왜
        깨졌는지 알 수 없게 된다 — 목록에 적힌 이름만 레일로 넘긴다.
        """
        if name in _RAIL_WIDGETS:
            return getattr(self.rail, name)
        raise AttributeError(name)

    def tab_widgets(self) -> list[QWidget]:
        """탭 순서대로 — 액션바·인스펙터 페이지 번호가 이 순서를 따른다."""
        return [self.tab_explore, self.tab_summary, self.tab_report]

    # ── 인스펙터 공용 [보기] ─────────────────────────────────
    def _build_view_section(self) -> ChromeSection:
        """표현만 바꾸는 것들 — DB를 다시 읽지 않으므로 [적용]이 필요 없다.

        예전에는 도크 `표시` 절에 있어서, 바로 위의 lot 선택([적용] 필요)과
        구분되지 않았다(설계 §0 D).
        """
        sec = ChromeSection("보기")
        lab = QLabel("로그 축 item 패턴")
        lab.setObjectName("hint")
        sec.body.addWidget(lab)
        self.ed_log = QLineEdit(", ".join(self.state.log_patterns))
        self.ed_log.setToolTip(
            "이름이 이 패턴에 걸리는 item은 Y축을 로그로 그립니다(쉼표로 여러 개).")
        self.ed_log.editingFinished.connect(self._log_changed)
        sec.body.addWidget(self.ed_log)

        self.chk_lot_split = QCheckBox("lot마다 심볼 다르게")
        self.chk_lot_split.setToolTip(
            "여러 lot을 함께 볼 때 lot마다 점 모양을 달리하고 범례에 lot을 적습니다.\n"
            "켜 두는 동안에는 그룹별로 지정한 심볼 대신 lot이 모양을 정합니다.")
        self.chk_lot_split.toggled.connect(self._lot_split_toggled)
        sec.body.addWidget(self.chk_lot_split)
        return sec

    def _tab_changed(self, index: int) -> None:
        self.actionbar.show_page(index)
        self.inspector.show_page(index)

    def _build_shortcuts(self) -> None:
        """이 화면의 동작 — 창 전역 단축키는 MainWindow가 갖는다."""
        for keys, fn in (("F5", self.apply_config),
                         ("Ctrl+Return", self._run_current_tab),
                         ("Ctrl+Enter", self._run_current_tab),
                         ("Ctrl+Z", self._undo),
                         ("F9", self.toggle_rail),
                         ("F10", self.toggle_inspector),
                         ("F2", self.toggle_guide)):
            QShortcut(QKeySequence(keys), self, activated=fn)

    # ── 패널 접기 (1366×768에서 캔버스를 되찾는 길) ───────────
    def toggle_rail(self) -> None:
        """F9 — 왼쪽 소스 레일을 접는다. 데이터셋을 정한 뒤에는 볼 일이 없다."""
        self.rail.setVisible(not self.rail.isVisible())

    def toggle_inspector(self) -> None:
        """F10 — 오른쪽 인스펙터를 접는다. 둘 다 접으면 캔버스가 약 1350px."""
        self.inspector.setVisible(not self.inspector.isVisible())

    # ── 입력 가이드 (설계 §5) ────────────────────────────────
    def requirements(self) -> list:
        """지금 무엇이 채워졌는지 — 판정은 `ui/guidance.py` 하나가 한다."""
        return guidance.analysis_requirements(self.state, self.cfg())

    def toggle_guide(self) -> bool:
        """`F2` — 다음에 할 한 곳만 강조한다. 다 채우면 저절로 꺼진다."""
        self._guide = not self._guide
        if self._guide and guidance.next_step(self.requirements()) is None:
            self._guide = False          # 채울 것이 없으면 켤 이유도 없다
            from etreport.ui.widgets.toast import toast
            toast(self, "채워야 할 입력이 없습니다 — [적용](F5)한 결과를 보세요")
        self._refresh_guidance()
        return self._guide

    def _refresh_guidance(self) -> None:
        """필요 표시(층 1)는 늘, 가이드 강조(층 2)는 켰을 때만."""
        reqs = self.requirements()
        step = guidance.next_step(reqs)
        if self._guide and step is None:         # 다 채웠다 — 스스로 꺼진다
            self._guide = False
        self.rail.apply_guidance(reqs, step.key if self._guide and step else "")
        if self._guide and step is not None:
            self.lbl_apply.setText(f"① {step.label} — {step.how}")

    def _run_current_tab(self) -> None:
        """보고 있는 탭의 주 동작([그리기]·[표 만들기]·[미리보기])을 누른다."""
        tab = self.tabs.currentWidget()
        btn = getattr(tab, getattr(tab, "stale_button_attr", ""), None)
        if btn is not None and btn.isEnabled():
            btn.click()

    # ── lot 선택 (§9.2) ──────────────────────────────────────
    def _load_lots(self, db_path: str) -> None:
        """DB의 lot 목록을 읽어 리스트를 채운다 — **[적용] 전에** 도는 조회다.

        lot·wafer 두 컬럼만 세므로 그룹 편집의 wafer 색인보다 가볍다. 그래서
        워커로 넘기지 않는다(진행 창이 뜨는 편이 오히려 거슬린다).
        """
        st = self.state
        st.lots_all = []
        if db_path and Path(db_path).exists():
            try:
                from etreport.data import loader
                idx = loader.lot_index(db_path)
                st.lots_all = [str(v) for v in idx["lot"].to_list()]
                self._lot_wafers = dict(zip(st.lots_all,
                                            idx["wafers"].to_list()))
            except Exception as e:                   # noqa: BLE001
                # DB가 잠겼거나 스키마가 낯설 수 있다. lot을 못 고를 뿐,
                # [적용]은 예전처럼 전부 읽으면 되므로 막지 않는다.
                self._lot_wafers = {}
                log.info("lot 목록 조회 실패(전체 읽기로 진행): %s", e)
        else:
            self._lot_wafers = {}

        # DB 경로로 못 읽었어도 이미 읽어 둔 프레임이 있으면 거기서 lot을 뽑는다
        # — 데모처럼 파일 없이 상태만 채운 경우에도 리스트가 비지 않게.
        if not st.lots_all and st.data is not None and "lot" in st.data.columns:
            st.lots_all = sorted({str(v) for v in st.data["lot"].to_list()
                                  if v is not None})

        # 기억해 둔 선택을 되살린다. 지금 DB에 없는 lot은 버린다 — 다시 적재해
        # lot 구성이 바뀌었을 수 있다.
        saved = self.settings.lot_selections.get(db_path, [])
        keep = [x for x in saved if x in st.lots_all]
        # 저장된 것이 없거나 결국 전부면 **빈 리스트 = 전부**로 둔다
        st.lots_selected = [] if len(keep) == len(st.lots_all) else keep
        self._fill_lot_list()

    def _fill_lot_list(self) -> None:
        st = self.state
        # 빈 선택 = 전부(§9.2) — 상태와 화면이 같은 규칙을 쓴다
        sel = set(st.lots_selected) or set(st.lots_all)
        self.lot_list.blockSignals(True)
        self.lot_list.clear()
        for lot in st.lots_all:
            n = getattr(self, "_lot_wafers", {}).get(lot)
            it = QListWidgetItem(f"  {lot}" + (f"   {n}장" if n else ""))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if lot in sel else Qt.Unchecked)
            it.setData(Qt.UserRole, lot)
            self.lot_list.addItem(it)
        self.lot_list.blockSignals(False)
        self._lot_filter(self.lot_search.text())
        self._refresh_lot_title()

    def _lot_filter(self, text: str) -> None:
        """검색어에 안 맞는 lot을 **숨기기만** 한다 — 체크 상태는 그대로."""
        q = text.strip().lower()
        for i in range(self.lot_list.count()):
            it = self.lot_list.item(i)
            it.setHidden(bool(q) and q not in str(it.data(Qt.UserRole)).lower())

    def _refresh_lot_title(self) -> None:
        st = self.state
        total = len(st.lots_all)
        if not total:
            self.lot_section.set_title("lot")
        else:
            self.lot_section.set_title(
                f"lot  {len(st.lots_selected) or total}/{total}")
        self.btn_coverage.setEnabled(bool(st.data is not None))

    def _lot_toggled(self, _item: QListWidgetItem) -> None:
        self._collect_lots()
        self._mark_unapplied("lot 선택이 바뀌었습니다 — [적용] (F5)")

    def _lot_check_all(self, on: bool) -> None:
        """[전체]/[해제]는 **지금 보이는 lot에만** 건다.

        검색으로 걸러 놓고 [전체]를 누르면 "찾은 것만 고르겠다"는 뜻이다.
        검색이 비어 있으면 전부 보이므로 예전과 동작이 같다.
        """
        self.lot_list.blockSignals(True)
        for i in range(self.lot_list.count()):
            it = self.lot_list.item(i)
            if not it.isHidden():
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.lot_list.blockSignals(False)
        self._lot_toggled(None)

    def _collect_lots(self) -> None:
        """체크 상태 → state와 설정.

        **전부 고른 상태는 빈 리스트로 둔다.** 그래야 ① 읽는 SQL이 예전과 똑같고
        (WHERE가 아예 안 붙는다) ② 이 화면을 띄운 뒤 적재로 lot이 늘어도 그 lot이
        조용히 빠지지 않는다 — 목록에 없던 lot을 IN에 적을 수는 없으니까.
        같은 이유로 설정에도 기록하지 않는다.
        """
        st = self.state
        picked = [self.lot_list.item(i).data(Qt.UserRole)
                  for i in range(self.lot_list.count())
                  if self.lot_list.item(i).checkState() == Qt.Checked]
        st.lots_selected = [] if len(picked) == len(st.lots_all) else picked
        path = self.cfg().db_path
        if path:
            if st.lots_selected:
                self.settings.lot_selections[path] = list(st.lots_selected)
            else:
                self.settings.lot_selections.pop(path, None)
        self._refresh_lot_title()

    def _open_coverage(self) -> None:
        from etreport.model.coverage import build as cov_build
        from etreport.ui.widgets.coverage_dialog import CoverageDialog
        CoverageDialog(cov_build(self.state), self).exec()

    def _lot_split_toggled(self, on: bool) -> None:
        """표시 옵션이라 DB를 다시 읽지 않는다 — 보고 있는 탭만 dirty로."""
        self.state.lot_split_symbols = on
        self.cfg().lot_split_symbols = on
        self.bus.groups_changed.emit()

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
        self.chk_lot_split.blockSignals(True)
        self.chk_lot_split.setChecked(bool(getattr(c, "lot_split_symbols", False)))
        self.chk_lot_split.blockSignals(False)
        self.state.lot_split_symbols = self.chk_lot_split.isChecked()
        self._sync_tukey()
        self._load_lots(c.db_path)          # DB가 바뀌면 lot 목록도 바뀐다
        self._sync_cond()
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
        c.lot_split_symbols = self.chk_lot_split.isChecked()
        c.tukey_enabled = self.chk_tukey.isChecked()
        c.tukey_k = self._tukey_k()
        c.tukey_scope = self.cmb_tukey_scope.currentData() or outliers.SCOPE_COND
        self._collect_cond(c)
        # lot 선택은 프리셋이 아니라 DB 경로별로 남긴다 — 프리셋을 바꿔도 같은
        # DB면 같은 lot을 보고 싶기 때문이다(§9.2).
        self._collect_lots()

    # ── 측정 조건 필터 (§ 분석 범위) ────────────────────────
    def _cond_values(self) -> dict[str, list[str]]:
        """콤보에 채울 값 — 읽어 둔 프레임이 있으면 그것, 없으면 DB에서 가볍게.

        `state.cond_choices`는 로딩이 **좁히기 전** 프레임으로 만들어 둔 것이라,
        조건을 걸어 놓은 상태에서도 목록이 줄어들지 않는다.
        """
        from etreport.data import loader
        if self.state.cond_choices:
            return self.state.cond_choices
        path = self.cfg().db_path
        return loader.cond_index(path) if path else {}

    def _sync_cond(self) -> None:
        """설정 → 콤보. 프리셋을 갈아타거나 DB를 새로 읽은 뒤 부른다."""
        from etreport.model import conditions
        c = self.cfg()
        vals = self._cond_values()
        saved = {"step": getattr(c, "cond_step", ""),
                 "site": getattr(c, "cond_site", ""),
                 "temp": getattr(c, "cond_temp", "")}
        for name, cmb in self.cond_combos.items():
            want = str(saved.get(name) or conditions.ALL)
            options = list(vals.get(name, []))
            # 저장해 둔 값이 지금 DB에 없더라도 목록에 남긴다 — 조용히 '전체'로
            # 풀리면 좁혀 놓은 줄 알고 전체 결과를 보게 된다.
            if want and want not in options:
                options.append(want)
            cmb.blockSignals(True)
            cmb.clear()
            cmb.addItem("전체", conditions.ALL)
            for v in options:
                cmb.addItem(conditions.pretty(v), v)   # 보이는 글자만 정리
            i = cmb.findData(want)
            cmb.setCurrentIndex(i if i >= 0 else 0)
            cmb.setEnabled(bool(options))
            cmb.blockSignals(False)
        self._refresh_cond_title()

    def _collect_cond(self, c: AnalysisConfig) -> None:
        from etreport.model import conditions
        picked = {name: (cmb.currentData() or conditions.ALL)
                  for name, cmb in self.cond_combos.items()}
        c.cond_step = picked.get("step", "")
        c.cond_site = picked.get("site", "")
        c.cond_temp = picked.get("temp", "")

    def _refresh_cond_title(self) -> None:
        from etreport.model import conditions
        txt = conditions.label({
            name: (cmb.currentData() or conditions.ALL)
            for name, cmb in self.cond_combos.items()})
        self.cond_section.set_title(f"측정 조건  {txt}" if txt else "측정 조건")
        self.btn_cond_clear.setEnabled(bool(txt))

    def _cond_changed(self, _name: str) -> None:
        """조건만 바꾸고 다시 읽지는 않는다 — lot 선택과 같은 지연 계산 규약."""
        self._collect_cond(self.cfg())
        self._refresh_cond_title()
        self._mark_unapplied("측정 조건이 바뀌었습니다 — [적용] (F5)")

    def _cond_clear(self) -> None:
        for cmb in self.cond_combos.values():
            cmb.blockSignals(True)
            cmb.setCurrentIndex(0)
            cmb.blockSignals(False)
        self._cond_changed("")

    # ── 이상치 필터 ─────────────────────────────────────────
    def _tukey_k(self) -> float:
        """콤보에 적힌 배수. 숫자가 아니면 기본값으로 물러선다(입력 중일 수 있다)."""
        try:
            k = float(self.cmb_tukey_k.currentText().strip())
        except ValueError:
            return outliers.DEFAULT_K
        return k if k > 0 else outliers.DEFAULT_K

    def _tukey_changed(self) -> None:
        """설정만 바꾸고 **다시 걸지는 않는다** — 지연 계산 규약.

        데이터를 버리는 동작이라 더더욱 [적용]을 눌렀을 때만 돌아야 한다.
        여기서 바로 걸면 배수를 타이핑하는 중간값(예: '4'를 치는 순간)으로도
        한 번씩 계산이 돈다.
        """
        c = self.cfg()
        c.tukey_enabled = self.chk_tukey.isChecked()
        c.tukey_k = self._tukey_k()
        c.tukey_scope = self.cmb_tukey_scope.currentData() or outliers.SCOPE_COND
        for w in (self.cmb_tukey_k, self.cmb_tukey_scope, self.btn_tukey_log):
            w.setEnabled(c.tukey_enabled or w is self.btn_tukey_log)
        self._mark_unapplied(
            f"이상치 필터 {outliers.TukeyConfig(c.tukey_enabled, c.tukey_k, c.tukey_scope).label()}"
            f" — [적용](F5)을 눌러 반영하세요")

    def _sync_tukey(self) -> None:
        """설정 → 입력칸. 프리셋을 갈아탈 때 부른다."""
        c = self.cfg()
        self.chk_tukey.blockSignals(True)
        self.chk_tukey.setChecked(bool(getattr(c, "tukey_enabled", False)))
        self.chk_tukey.blockSignals(False)
        self.cmb_tukey_k.blockSignals(True)
        self.cmb_tukey_k.setCurrentText(f"{getattr(c, 'tukey_k', 3.0):g}")
        self.cmb_tukey_k.blockSignals(False)
        scope = getattr(c, "tukey_scope", outliers.SCOPE_COND)
        idx = self.cmb_tukey_scope.findData(scope)
        if idx >= 0:
            self.cmb_tukey_scope.blockSignals(True)
            self.cmb_tukey_scope.setCurrentIndex(idx)
            self.cmb_tukey_scope.blockSignals(False)
        for w in (self.cmb_tukey_k, self.cmb_tukey_scope):
            w.setEnabled(self.chk_tukey.isChecked())

    def _open_apply_log(self) -> None:
        """마지막 [적용]에서 건너뛴 행·확인할 것 — 출처별로 표에 펼친다."""
        import re

        import polars as pl

        from etreport.ui.widgets.table_dialog import FrameDialog
        rep = getattr(self, "_last_report", None)
        if rep is None:
            return
        rows = []
        for kind, items in (("제외", rep.warnings),
                            ("확인", getattr(rep, "notes", None) or [])):
            for text in items:
                m = re.match(r"\[(.+?)\]\s*(.*)", text, re.S)
                rows.append((kind, *(m.groups() if m else ("", text))))
        df = pl.DataFrame(rows, schema=["구분", "출처", "내용"], orient="row")
        FrameDialog(df, "적용 결과", self).exec()

    def _open_filter_log(self) -> None:
        """걸러진 점 목록 — 무엇이 왜 빠졌는지 확인하는 자리."""
        from etreport.model import outliers as ol
        from etreport.ui.widgets.table_dialog import FrameDialog
        df = ol.frame(self.state)
        if df.is_empty():
            QMessageBox.information(
                self, "이상치 필터",
                "걸러진 점이 없습니다.\n\n"
                "필터를 켜고 [적용](F5)을 누르면 여기에 이력이 쌓입니다.")
            return
        cfg = getattr(self.state, "tukey", None) or ol.TukeyConfig()
        FrameDialog(df, f"이상치 필터 이력 — {cfg.label()}", self).exec()

    # ── 파일 고르기 (읽지 않는다) ────────────────────────────
    def _pick_db(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "DuckDB 파일", "",
                                           "DuckDB (*.duckdb)")
        if p:
            self.cfg().db_path = p
            self._load_lots(p)             # 고르는 즉시 lot을 보여 준다
            self._mark_unapplied()

    def _pick_tpl(self, kind: str) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self, f"{kind} 템플릿", "",
            "템플릿 (*.xlsx *.xlsm *.csv *.tsv);;Excel (*.xlsx *.xlsm);;CSV (*.csv *.tsv)")
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
                                text=getattr(c, "split_text", ""),
                                baseline=getattr(c, "split_baseline", ""),
                                baseline_lot=getattr(c, "split_baseline_lot", ""),
                                lots=list(self.state.lots_selected),
                                state=self.state)
        if dlg.exec() and dlg.matrix is not None:
            c.split_path, c.split_text = dlg.path, dlg.text
            c.split_baseline = dlg.baseline
            c.split_baseline_lot = dlg.baseline_lot
            sm = dlg.matrix
            self._mark_unapplied(
                f"실험 조건 {len(sm.steps)}개 step · wafer {sm.wide.height}행을 "
                f"읽었습니다 — [적용]을 눌러 그룹에 반영하세요")

    # ── 적용 (검증은 여기서 한 번) ───────────────────────────
    def _mark_unapplied(self, msg: str = "바뀐 설정이 있습니다 — [적용] (F5)") -> None:
        from etreport.ui.tabs.common import set_dirty
        self._applied = False
        self.state.applied = False
        self.state.status_note = "미적용"
        set_dirty(self.btn_apply, True)
        self.lbl_apply.setText(msg)
        self.bus.status_changed.emit()
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
            self, "설정 적용",
            lambda report: apply_config(self.state, c, on_progress=report),
            done=lambda rep: self._apply_done(c, rep), needs_com=True,
            with_progress=True)
        # 실패해도 잠금은 풀려야 한다 — done은 성공했을 때만 불린다
        w.finished.connect(self._apply_unlock)

    def _apply_unlock(self) -> None:
        from etreport.ui.tabs.common import set_dirty
        self.setEnabled(True)
        self.btn_apply.setEnabled(True)
        self.btn_apply.setText("적용")
        set_dirty(self.btn_apply, not self._applied)   # 실패해도 표시는 남는다

    def _apply_done(self, c, rep) -> None:
        """워커가 끝난 뒤 UI 반영 — 여기서만 위젯을 만진다."""
        self._apply_unlock()
        if not rep.ok:
            self.lbl_apply.setText("적용 실패")
            QMessageBox.critical(self, "적용 실패", rep.text())
            return

        from etreport.ui.tabs.common import set_dirty
        self._applied = True
        self.state.applied = True
        self.state.status_note = ""
        set_dirty(self.btn_apply, False)
        self.lbl_apply.setText(
            f"적용됨 · {rep.elapsed:.1f}초"
            + (f" · 제외 {len(rep.warnings)}건" if rep.warnings else "")
            + (f" · 확인 {len(rep.notes)}건" if getattr(rep, "notes", None)
               else ""))
        self.bus.status_changed.emit()

        self._show_report(c.report)

        # split 배정은 loader.load_state가 이미 했다(그 뒤에 manual_groups까지
        # 재적용). 여기서 _apply_split을 또 부르면 그룹 편집에서 만든 그룹과
        # 손배정이 통째로 덮어써진다 — [적용] 후 그룹이 초기화되던 원인.
        self.settings.save()
        # 읽고 나면 조건 목록이 **좁히기 전 프레임 기준**으로 채워져 있다 —
        # 콤보를 그때 다시 만들어야 고른 값 말고 다른 값도 고를 수 있다.
        self._sync_cond()
        self.bus.data_changed.emit()
        self.bus.report_changed.emit()
        self._refresh_dock()

        # 성공한 [적용]은 모달로 막지 않는다 — 건너뛴 행이 많으면 매번 긴 창을
        # 닫아야 해서 피로했다. 한 줄로 알리고 자세한 것은 레일 버튼으로 연다.
        self._last_report = rep
        notes = getattr(rep, "notes", None) or []
        self.btn_apply_log.setVisible(bool(rep.warnings or notes))
        if rep.warnings or notes:
            from etreport.ui.widgets.toast import toast
            parts = ([f"제외 {len(rep.warnings)}건"] if rep.warnings else []) \
                + ([f"확인 {len(notes)}건"] if notes else [])
            toast(self, f"적용 완료 — {' · '.join(parts)} · "
                        f"왼쪽 [적용 결과 보기]에서 확인하세요")

    def _show_report(self, name: str) -> None:
        """적용된 리포트 이름을 문구로만 보여 준다 — 고르는 콤보는 없다(§5.1).

        한 파일에 리포트가 여럿이면 그 **사실만** 한 줄로 알리고, 목록과 바꾸는
        방법은 툴팁에 둔다(설계 §2 — 조작면에 산문이 상주할 이유가 없다). 레일이
        244px라 4줄짜리 문구가 상주하면 그만큼 소스 목록이 잘린다.
        """
        others = [r for r in self.state.reports if r != name]
        if not name:
            self.lbl_report.setText("")
            self.lbl_report.setToolTip("")
            return
        text = f"REPORT  {name}"
        tip = f"적용된 리포트: {name}"
        if others:
            text += f"  · 리포트 {len(self.state.reports)}개"
            tip = (f"{tip}\n템플릿에 리포트가 {len(self.state.reports)}개 있습니다 "
                   f"({', '.join(others[:3])}{'…' if len(others) > 3 else ''}).\n"
                   f"다른 리포트를 쓰려면 템플릿의 Report 열을 바꾸세요.")
        self.lbl_report.setText(text)
        self.lbl_report.setToolTip(tip)

    # ── 표시 갱신 ────────────────────────────────────────────
    def _refresh_dock(self) -> None:
        st, c = self.state, self.cfg()

        def short(p: str) -> str:
            return Path(p).name if p else ""

        self.rail.set_file("db", short(c.db_path), st.table)
        self.rail.set_file("plot", short(c.plot_template_path), c.plot_sheet)
        self.rail.set_file("tbl", short(c.table_template_path), c.table_sheet)
        self.rail.set_file("rfm", short(c.reformatter_path), c.reformatter_sheet)
        self.rail.set_file("split", "붙여넣은 내용" if getattr(c, "split_text", "")
                           else short(c.split_path))

        # factor는 그룹이 만들어지는 **근거**라 레일에 남는다. 그 결과(목록·색·
        # 보이기·혼입 경고)는 인스펙터 [그룹] 섹션이 스스로 갱신한다.
        self.lbl_factor.setText(
            f"factor · {', '.join(st.factors) or '(없음)'}" if st.split
            else "실험 조건 파일 미연결")

        # 붙여 둔 추가 소스 — 접힌 절 안이라 한 줄로 알린다
        extra = ([f"계측 {len(st.met_columns)}열"] if st.met_columns else []) \
            + ([f"tracking {len(st.track_columns)}열"] if st.track_columns else [])
        self.lbl_sources.setText(" · ".join(extra) or "붙인 컬럼 없음")

        # 데이터가 들어온 뒤에야 lot을 알 수 있는 경로(적재 직후·데모)를 위해
        # 목록이 비어 있으면 여기서 한 번 더 채운다. 조회는 하지 않는다.
        # lot 컬럼이 있을 때만 — 그래야 채우고 나면 다시 돌지 않는다(무한 조회 방지)
        if not st.lots_all and st.data is not None and "lot" in st.data.columns:
            self._load_lots(c.db_path)
        self._refresh_lot_title()
        self.btn_undo.setEnabled(bool(st.undo_stack))

        # 이상치 절은 **접힌 제목이 상태를 말한다** — 펼치지 않고도 켜져 있는지,
        # 몇 점이 걸렸는지 보인다(lot 절과 같은 관용구).
        cfg = getattr(st, "tukey", None) or outliers.TukeyConfig()
        n_f = len(getattr(st, "filtered", {}) or {})
        self.tukey_section.set_title(
            f"이상치  {cfg.label()} · {n_f:,}점" if cfg.enabled else "이상치  꺼짐")
        self.btn_tukey_log.setEnabled(bool(n_f))
        for w in (self.cmb_tukey_k, self.cmb_tukey_scope):
            w.setEnabled(self.chk_tukey.isChecked())

        # 하단 요약 한 줄 — 큰 숫자는 상단 상태 레일이 갖는다
        parts = [f"그룹 {len(st.groups)}"] if st.groups else []
        if st.excluded:
            parts.append(f"제외 {len(st.excluded)}")
        if n_f:
            parts.append(f"필터 {n_f:,}")
        self.lbl_summary.setText(" · ".join(parts))
        self._refresh_guidance()

    # ── 나머지 동작 ──────────────────────────────────────────
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
            from etreport.model import wafers
            assign = st.split.assignment(st.factors)
            st.data = st.data.with_columns(pl.Series(
                "gid", wafers.map_gids(st.data["lot"], st.data["wafer"], assign)))
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
        from etreport.ui.widgets.toast import toast
        n = xlio.invalidate()
        self._refresh_dock()
        toast(self, f"Excel 캐시 {n}개를 비웠습니다 — 다음 읽기는 Excel을 엽니다")

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

    def _open_fabtrack(self) -> None:
        """fab tracking 창(기능 A) — 이름 붙인 컬럼을 분석 프레임에 붙인다.

        [적용]으로 눌렀을 때만 붙인다(창 안에서 처리). 붙은 뒤에는 축 후보와 표가
        달라지므로 화면에 알린다 — 지연 계산 규약대로 **다시 그리라고 표시만** 하고
        여기서 그리지는 않는다.
        """
        from etreport.ui.widgets.fabtrack_dialog import FabTrackDialog
        from etreport.ui.widgets.toast import toast
        st = self.state
        if st.data is None:
            QMessageBox.information(
                self, "fab tracking",
                "먼저 [적용](F5)으로 분석 DB를 여세요 — 붙일 대상이 필요합니다")
            return
        dlg = FabTrackDialog(st, self, lots=list(st.lots_selected))
        if not dlg.exec():
            return
        names = dlg.apply_to_state()
        if names:
            toast(self, f"fab tracking 컬럼 {len(names)}개를 붙였습니다 "
                        f"({', '.join(names[:4])}{'…' if len(names) > 4 else ''}) "
                        f"— boxplot X축·표 범주에서 쓸 수 있습니다")
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
        # 방금 적재한 DB다. lot 목록을 먼저 읽어야 [적용]이 무엇을 볼지 정해진다 —
        # 기억해 둔 선택이 있으면 그대로, 없으면 전부.
        self._load_lots(path)
        self.apply_config()
