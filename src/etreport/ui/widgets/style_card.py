"""그룹 스타일 카드 — 색·심볼·크기·REF를 고르는 공용 위젯.

탐색 탭과 리포트 구성 탭이 **같은 위젯**을 쓴다. 스타일은 도크·그룹 편집·이
카드 세 곳에서 바뀔 수 있으므로 변경은 `groups_changed` **한 방향**으로만
알리고, 그 신호를 되받아 컨트롤을 채울 때는 시그널을 막아 재귀를 피한다.
"""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from etreport.model.split import SYMBOLS
from etreport.model.state import AppState, StateBus
from etreport.ui.tabs.common import on_combo
from etreport.ui.widgets.cards import Card

SYMBOL_LABELS = ["● 원", "■ 사각", "▲ 삼각", "◆ 마름모", "＋ 십자"]
SIZES = ["4", "6", "8", "10", "12"]


class GroupStyleCard(Card):
    """그룹 하나를 골라 색·심볼·크기·REF를 바꾼다."""

    def __init__(self, state: AppState, bus: StateBus,
                 on_changed=None, parent=None) -> None:
        super().__init__("그룹 스타일", parent=parent)
        self.state, self.bus = state, bus
        self._on_changed = on_changed        # 카드를 쓰는 탭이 다시 그릴 때
        self._syncing = False

        self.cmb_group = QComboBox()
        on_combo(self.cmb_group, self.to_controls)
        self.body.addWidget(self.cmb_group)

        self.btn_color = QPushButton("색")
        self.btn_color.clicked.connect(self._pick_color)
        self.cmb_symbol = QComboBox()
        self.cmb_symbol.addItems(SYMBOL_LABELS)
        on_combo(self.cmb_symbol, self.from_controls)
        self.cmb_size = QComboBox()
        self.cmb_size.addItems(SIZES)
        on_combo(self.cmb_size, self.from_controls)
        h = QHBoxLayout()
        h.addWidget(self.btn_color, 1)
        h.addWidget(self.cmb_symbol, 1)
        h.addWidget(self.cmb_size)
        w = QWidget()
        w.setLayout(h)
        self.body.addWidget(w)

        self.chk_ref = QCheckBox("이 그룹을 REF로")
        self.chk_ref.setToolTip("REF는 회색으로 그리고 Δ의 기준이 됩니다.")
        self.chk_ref.toggled.connect(self.from_controls)
        self.body.addWidget(self.chk_ref)

        self.btn_all = QPushButton("모든 plot에 적용")
        self.btn_all.setToolTip(
            "지금 고른 그룹의 심볼·크기를 **모든 그룹**에 똑같이 적용하고\n"
            "모든 plot(탐색·리포트 슬롯·PPT)을 다시 그립니다.\n"
            "색은 그룹을 구분하는 값이라 그대로 둡니다.")
        self.btn_all.clicked.connect(self.apply_to_all)
        self.body.addWidget(self.btn_all)

        bus.groups_changed.connect(self._external_change)
        self.fill_groups()

    # ── 목록 ─────────────────────────────────────────────────
    def fill_groups(self) -> None:
        cmb = self.cmb_group
        cur = cmb.currentIndex()
        cmb.blockSignals(True)
        cmb.clear()
        cmb.addItems([g.name for g in self.state.groups])
        if self.state.groups:
            cmb.setCurrentIndex(min(max(cur, 0), len(self.state.groups) - 1))
        cmb.blockSignals(False)
        self.to_controls()

    def current(self):
        """고른 그룹 — 없으면 None(인덱스를 직접 쓰지 않는다)."""
        i = self.cmb_group.currentIndex()
        if 0 <= i < len(self.state.groups):
            return self.state.groups[i]
        return None

    # ── 상태 ↔ 컨트롤 ────────────────────────────────────────
    def to_controls(self) -> None:
        g = self.current()
        for w in (self.cmb_symbol, self.cmb_size, self.chk_ref):
            w.blockSignals(True)
        for w in (self.btn_color, self.cmb_symbol, self.cmb_size, self.chk_ref,
                  self.btn_all):
            w.setEnabled(g is not None)
        if g is not None:
            self.btn_color.setStyleSheet(
                f"background:{g.color}; color:white; border-radius:6px;")
            self.cmb_symbol.setCurrentIndex(
                SYMBOLS.index(g.symbol) if g.symbol in SYMBOLS else 0)
            i = self.cmb_size.findText(str(g.size))
            self.cmb_size.setCurrentIndex(i if i >= 0 else 1)
            self.chk_ref.setChecked(g.ref)
        for w in (self.cmb_symbol, self.cmb_size, self.chk_ref):
            w.blockSignals(False)

    def from_controls(self) -> None:
        g = self.current()
        if g is None:
            return
        g.symbol = SYMBOLS[self.cmb_symbol.currentIndex()]
        g.size = int(self.cmb_size.currentText())
        if self.chk_ref.isChecked():
            for other in self.state.groups:      # REF는 하나뿐
                other.ref = other is g
        else:
            g.ref = False
        self._announce()

    def apply_to_all(self) -> int:
        """고른 그룹의 심볼·크기를 모든 그룹에 건다. 바뀐 그룹 수를 반환.

        그룹 스타일은 원래 plot 전체가 공유하지만, 슬롯마다 따로 정하는
        줄 알고 매번 다시 고르는 일이 잦았다(요청 §7). 한 번에 맞추고 즉시
        다시 그리도록 명시적인 버튼을 둔다.
        """
        g = self.current()
        if g is None:
            return 0
        for other in self.state.groups:
            other.symbol, other.size = g.symbol, g.size
        self._announce()
        return len(self.state.groups)

    def _pick_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog
        g = self.current()
        if g is None:
            return
        c = QColorDialog.getColor(QColor(g.color), self, "그룹 색")
        if c.isValid():
            g.color = c.name()
            self.to_controls()
            self._announce()

    # ── 동기화 ───────────────────────────────────────────────
    def _announce(self) -> None:
        self._syncing = True
        try:
            self.bus.groups_changed.emit()
        finally:
            self._syncing = False
        if self._on_changed is not None:
            self._on_changed()

    def _external_change(self) -> None:
        if self._syncing:                        # 내가 쏜 신호는 되받지 않는다
            return
        self.fill_groups()
