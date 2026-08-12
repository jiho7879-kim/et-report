"""탐색 탭 — 자유 산점도 + 클릭 제외.

축을 직접 입력해 그려 보고, 이상해 보이는 점을 클릭해 바로 제외한다.
그림은 [그리기]를 누를 때(또는 이 화면을 보고 있을 때) 그린다.
"""
from __future__ import annotations

import copy

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from etreport.data.loader import RESERVED, item_columns
from etreport.model.state import AppState, StateBus
from etreport.ui.tabs.common import StaleMixin
from etreport.ui.widgets.autocomplete import AutoCompleteEdit
from etreport.ui.widgets.cards import Card, GhostButton, row
from etreport.ui.widgets.plot_canvas import PlotCanvas


class ExploreTab(StaleMixin, QWidget):
    stale_button_attr = "btn_draw"
    stale_label_attr = "lbl_info"
    stale_message = "변경됨 — [그리기]"

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
                return item_columns(state.data)
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

    # ── 지연 계산 (규약은 tabs/common.StaleMixin) ────────────
    def _on_exclusion(self) -> None:
        # 이 화면에서 찍은 제외는 바로 반영, 아니면 표시만
        if self.isVisible():
            self.redraw()
        else:
            self.mark_stale()

    def _axes_changed(self) -> None:
        self.state.explore.x = self.ed_x.text()
        self.state.explore.y = self.ed_y.text()
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

    def refresh(self) -> None:
        """StaleMixin이 부르는 갱신 진입점."""
        self.redraw()

    def redraw(self) -> None:
        st = self.state
        self.mark_fresh()
        if st.data is None:
            self.lbl_info.setText(
                "데이터 없음 — [데이터]에서 추출·적재하거나 도크에서 DB를 여세요")
            self.canvas.figure.clear()
            self.canvas.draw_idle()
            return
        if not st.explore.x and st.data.width > len(RESERVED):
            items = item_columns(st.data)
            st.explore.x, st.explore.y = items[0], items[min(1, len(items) - 1)]
        for ed, val in ((self.ed_x, st.explore.x), (self.ed_y, st.explore.y)):
            if not ed.hasFocus() and ed.text() != val:
                ed.setText(val)
        self.canvas.draw_spec(st.explore)
        n = st.data.height
        self.lbl_info.setText(f"{n - len(st.excluded):,} / {n:,} 포인트")
