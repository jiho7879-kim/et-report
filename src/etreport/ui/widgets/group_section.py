"""그룹 섹션 — 흩어져 있던 넷을 한 곳으로(설계 §1 규칙 2, §3).

예전에는 같은 개념이 세 군데에 있었다: *보이기*는 도크 리스트, *스타일*은
탐색 인스펙터와 리포트 인스펙터에 **각각 한 벌**, *편집*은 도크 버튼. 리포트
화면에는 뜻이 다른 `[모든 plot에 적용]`이 둘이나 보였다. 이제 이 섹션 하나가
`보이기 · 색/심볼/크기 · REF · 편집`을 전부 갖고, **워크스페이스가 한 개만**
만들어 인스펙터 공용 자리에 둔다.

리스트에서 고른 행이 곧 편집 대상이라 **그룹 고르기 콤보가 사라졌다** — 도크
리스트와 인스펙터 콤보가 같은 일을 두 번 하고 있었다.

스타일은 여기 말고도 그룹 편집 창·실험 조건 배정에서 바뀔 수 있으므로 변경은
`groups_changed` **한 방향**으로만 알리고, 그 신호를 되받아 컨트롤을 채울 때는
시그널을 막아 재귀를 피한다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QWidget,
)

from etreport.model.split import SYMBOLS
from etreport.model.state import AppState, StateBus
from etreport.ui.tabs.common import on_combo
from etreport.ui.widgets.cards import ChromeSection, GhostButton

__all__ = ["SIZES", "SYMBOL_LABELS", "GroupSection", "swatch_qss"]

SYMBOL_LABELS = ["● 원", "■ 사각", "▲ 삼각", "◆ 마름모", "＋ 십자"]
SIZES = ["4", "6", "8", "10", "12"]

#: 색 견본과 크기 콤보의 폭. 셋을 균등하게 나누면 '◆ 마름모'가 '◆ 마'로
#: 잘린다 — 콤보는 줄임표 없이 그냥 자른다. 글자 수가 정해진 두 칸을 묶어
#: 두고 남는 자리를 전부 심볼 콤보에 준다.
_SWATCH_W = 40
_SIZE_W = 50


def swatch_qss(color: str) -> str:
    """그룹 색을 칠한 견본 버튼의 스타일.

    글자색을 흰색으로 고정하면 Okabe-Ito의 밝은 노랑·하늘색 위에서 '색'이
    읽히지 않는다. 배경 밝기를 보고 먹/흰 중 대비가 큰 쪽을 고른다.
    """
    from etreport.ui.theme import TOKENS

    c = QColor(color)
    # 상대 휘도 근사(sRGB 가중). 0.55를 넘으면 밝은 색으로 본다.
    lum = (0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF())
    fg = TOKENS["TEXT"] if lum > 0.55 else TOKENS["PAPER"]
    # **가로 padding을 반드시 덮어쓴다.** 기본 버튼 규칙이 좌우 18px씩을 먹어
    # 40px 견본에는 글자 자리가 한 톨도 남지 않는다(글자가 세로 막대로 뭉갠다).
    return (f"background:{c.name()}; color:{fg};"
            f" border:none; border-radius:{TOKENS['RADIUS']};"
            f" padding:6px 2px;")


class GroupSection(ChromeSection):
    """체크로 보이기, 고른 행으로 스타일 편집, 아래 두 버튼으로 일괄·편집."""

    def __init__(self, state: AppState, bus: StateBus,
                 on_changed=None, on_edit=None, parent=None) -> None:
        super().__init__("그룹", parent=parent)
        self.state, self.bus = state, bus
        self._on_changed = on_changed      # 보고 있는 탭을 다시 그리는 콜백
        self._on_edit = on_edit            # [그룹 편집…] — 창은 워크스페이스가 연다
        self._syncing = False

        self.lbl_count = QLabel()
        self.lbl_count.setObjectName("cardSub")
        self.head.addWidget(self.lbl_count)

        # 체크 = 보이기, 선택(하이라이트) = 편집 대상. 둘을 한 리스트에 둔다.
        self.list = QListWidget()
        self.list.setObjectName("groupList")
        self.list.setFixedHeight(108)
        self.list.itemChanged.connect(self._item_changed)
        self.list.currentRowChanged.connect(lambda _i: self.to_controls())
        self.body.addWidget(self.list)

        self.btn_color = QPushButton("색")
        self.btn_color.setFixedWidth(_SWATCH_W)
        self.btn_color.setToolTip("고른 그룹의 점 색을 고릅니다.")
        self.btn_color.clicked.connect(self._pick_color)
        self.cmb_symbol = QComboBox()
        self.cmb_symbol.addItems(SYMBOL_LABELS)
        on_combo(self.cmb_symbol, self.from_controls)
        self.cmb_size = QComboBox()
        self.cmb_size.addItems(SIZES)
        self.cmb_size.setToolTip("점 크기(pt).")
        self.cmb_size.setFixedWidth(_SIZE_W)
        on_combo(self.cmb_size, self.from_controls)
        h = QHBoxLayout()
        # 기본 여백(9px)을 지우지 않으면 이 줄만 위아래 콤보보다 안쪽으로
        # 들어가 왼쪽 선이 어긋나고, 그 18px 때문에 심볼 콤보가 잘린다.
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.btn_color)
        h.addWidget(self.cmb_symbol, 1)
        h.addWidget(self.cmb_size)
        w = QWidget()
        w.setLayout(h)
        self.body.addWidget(w)

        self.chk_ref = QCheckBox("이 그룹을 REF로")
        self.chk_ref.setToolTip("REF는 회색으로 그리고 Δ의 기준이 됩니다.")
        self.chk_ref.toggled.connect(self.from_controls)
        self.body.addWidget(self.chk_ref)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.btn_all = GhostButton("스타일을 전 plot에")
        self.btn_all.setToolTip(
            "지금 고른 그룹의 심볼·크기를 **모든 그룹**에 똑같이 적용하고\n"
            "모든 plot(탐색·리포트 슬롯·PPT)을 다시 그립니다.\n"
            "색은 그룹을 구분하는 값이라 그대로 둡니다.")
        self.btn_all.clicked.connect(self.apply_to_all)
        row.addWidget(self.btn_all, 1)
        self.btn_edit = GhostButton("그룹 편집…")
        self.btn_edit.setToolTip(
            "wafer를 그룹에 손으로 배정하고 그룹을 만들거나 지웁니다.")
        self.btn_edit.clicked.connect(self._edit)
        row.addWidget(self.btn_edit)
        w2 = QWidget()
        w2.setLayout(row)
        self.body.addWidget(w2)

        # 혼입 경고 — 그룹핑의 결과를 말하는 자리라 그룹 옆에 붙는다
        self.lbl_confound = QLabel()
        self.lbl_confound.setObjectName("warn")
        self.lbl_confound.setWordWrap(True)
        self.body.addWidget(self.lbl_confound)

        bus.groups_changed.connect(self._external_change)
        for sig in (bus.data_changed, bus.exclusion_changed):
            sig.connect(self.fill_groups)
        self.fill_groups()

    # ── 목록 ─────────────────────────────────────────────────
    def fill_groups(self) -> None:
        """상태 → 리스트. 포인트 수는 `group_by` 한 번으로 센다.

        제외를 찍을 때마다 불리는 자리라 그룹마다 전체 프레임을 필터하면
        데이터가 커질수록 클릭이 무거워진다.
        """
        st = self.state
        counts: dict[str, int] = {}
        if st.data is not None and st.groups:
            counts = {r["gid"]: r["len"] for r in
                      st.active().group_by("gid").len().iter_rows(named=True)}
        cur = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for g in st.groups:
            it = QListWidgetItem(
                f" {g.name}{'  · REF' if g.ref else ''}   {counts.get(g.gid, 0)}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if g.visible else Qt.Unchecked)
            it.setForeground(QColor(g.color))
            it.setData(Qt.UserRole, g.gid)
            self.list.addItem(it)
        if st.groups:
            self.list.setCurrentRow(min(max(cur, 0), len(st.groups) - 1))
        self.list.blockSignals(False)
        self.lbl_count.setText(f"{len(st.groups)}개" if st.groups else "")

        cf = st.split.confounds(st.factors) if st.split else []
        self.lbl_confound.setVisible(bool(cf))
        if cf:
            self.lbl_confound.setText(
                f"⚠ 혼입 {len(cf)}건 — '{cf[0].group}' 안에 "
                f"{cf[0].step}가 {len(cf[0].codes)}종 섞여 있습니다")
        self.to_controls()

    def select(self, index: int) -> None:
        """편집 대상 고르기 — 예전 `그룹 고르기` 콤보를 대신한다."""
        self.list.setCurrentRow(index)

    def current(self):
        """고른 그룹 — 없으면 None(인덱스를 직접 쓰지 않는다)."""
        i = self.list.currentRow()
        if 0 <= i < len(self.state.groups):
            return self.state.groups[i]
        return None

    def _item_changed(self, item: QListWidgetItem) -> None:
        """체크 = 보이기. 즉시 반영한다(확정 §3 — 그룹 토글은 지연 계산 예외)."""
        g = self.state.group(item.data(Qt.UserRole))
        if g is None:
            return
        g.visible = item.checkState() == Qt.Checked
        self._announce()

    # ── 상태 ↔ 컨트롤 ────────────────────────────────────────
    def to_controls(self) -> None:
        g = self.current()
        for w in (self.cmb_symbol, self.cmb_size, self.chk_ref):
            w.blockSignals(True)
        for w in (self.btn_color, self.cmb_symbol, self.cmb_size, self.chk_ref,
                  self.btn_all):
            w.setEnabled(g is not None)
        if g is not None:
            self.btn_color.setStyleSheet(swatch_qss(g.color))
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
        줄 알고 매번 다시 고르는 일이 잦았다(요청 §7).
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

    def _edit(self) -> None:
        if self._on_edit is not None:
            self._on_edit()

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
