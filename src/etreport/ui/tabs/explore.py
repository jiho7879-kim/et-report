"""탐색 탭 — 자유 산점도 + 클릭 제외.

축을 직접 입력해 그려 보고, 이상해 보이는 점을 클릭해 바로 제외한다.
그림은 [그리기]를 누를 때(또는 이 화면을 보고 있을 때) 그린다.
"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from etreport.data.loader import RESERVED, item_columns
from etreport.model.specs import (
    GEOM_COLUMNS,
    PLOT_TYPE_LABELS,
    PLOT_TYPES,
    POINT_MODES,
)
from etreport.model.state import AppState, StateBus
from etreport.ui.actionbar import ActionItems
from etreport.ui.tabs.common import StaleMixin, on_combo
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
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 14)
        lay.setSpacing(8)

        # 캔버스 위에 남는 것은 제외/복원 토글 하나다 — [그리기]·[＋ 리포트에
        # 추가]·포인트 수는 하단 액션바가 갖는다(설계 §1 규칙 3). 여기서 만들고
        # 주소만 넘긴다: 버튼을 액션바에서 새로 만들면 dirty 표시가 두 벌이 된다.
        bar = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["클릭 → 제외", "클릭 → 복원"])
        self.mode.setToolTip("캔버스의 점을 클릭했을 때 무엇을 할지")
        bar.addWidget(self.mode)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.btn_draw = QPushButton("그리기")
        self.btn_draw.setToolTip("고른 축으로 다시 그립니다 (Ctrl+Enter)")
        self.btn_draw.clicked.connect(self.redraw)
        self.lbl_info = QLabel()
        self.lbl_info.setObjectName("hint")
        self.btn_add = GhostButton("＋ 리포트에 추가")
        self.btn_add.clicked.connect(self._add_to_report)
        self._stale = True

        # 캔버스와 빈 상태를 겹쳐 두고 갈아 끼운다 — 아무것도 안 물린 상태에서
        # 빈 회색 캔버스만 보이면 무엇을 눌러야 하는지 알 수 없다(설계 §0 G).
        self.canvas = PlotCanvas(state)
        self.canvas.on_pick = self._pick
        self.empty = QLabel()
        self.empty.setObjectName("emptyHint")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.body = QStackedWidget()
        self.body.addWidget(self.canvas)
        self.body.addWidget(self.empty)
        lay.addWidget(self.body, 1)

        # ── 인스펙터 섹션 — 패널은 워크스페이스가 하나만 소유한다 ──
        # 카드 위젯은 그대로 쓰되 objectName을 바꿔 `#card` 흰 배경을 벗는다.
        self._sections = [self._axis_card(), self._scale_card()]

        bus.groups_changed.connect(self._groups_changed)
        for sig in (bus.data_changed, bus.explore_changed):
            sig.connect(self.mark_stale)
        bus.exclusion_changed.connect(self._on_exclusion)
        self.mark_stale()

    # ── 워크스페이스에 넘기는 것 ─────────────────────────────
    def action_items(self) -> ActionItems:
        return ActionItems(primary=self.btn_draw, status=self.lbl_info,
                           extra=[self.btn_add])

    def inspector_sections(self) -> list[QWidget]:
        return self._sections

    # ── 축 카드 ──────────────────────────────────────────────
    def _axis_card(self) -> Card:
        state = self.state
        card = Card("축")
        card.setObjectName("probeSection")
        # plot 종류 — 템플릿의 Type 열과 같은 값(model/specs.PLOT_TYPES).
        # 종류를 바꾸면 X가 뜻하는 것이 달라지므로(item ↔ 범주 ↔ 기하) 자동완성
        # 후보와 안내 문구도 함께 바뀐다.
        self.cmb_type = QComboBox()
        for t in PLOT_TYPES:
            self.cmb_type.addItem(PLOT_TYPE_LABELS[t], t)
        self.cmb_type.setToolTip(
            "산점도 — X·Y 모두 item\n"
            "boxplot — X는 나눌 기준(lot+wafer·그룹·온도·fab tracking 컬럼…)\n"
            "기하 trend — X는 W 또는 L")
        self.cmb_type.setCurrentIndex(
            PLOT_TYPES.index(state.explore.type)
            if state.explore.type in PLOT_TYPES else 0)
        on_combo(self.cmb_type, self._type_changed)
        card.body.addWidget(row("종류", self.cmb_type, stretch_at=1))
        self.ed_x = AutoCompleteEdit(self._x_items)
        self.ed_y = AutoCompleteEdit(self._y_items)
        self.ed_x.setText(state.explore.x)
        self.ed_y.setText(state.explore.y)
        self.ed_x.editingFinished.connect(self._axes_changed)
        self.ed_y.editingFinished.connect(self._axes_changed)
        card.body.addWidget(row("X", self.ed_x, stretch_at=1))
        card.body.addWidget(row("Y", self.ed_y, stretch_at=1))
        self.cmb_point = QComboBox()
        self.cmb_point.addItems(["측정점 그대로", "wafer 평균", "wafer 중앙값",
                                 "wafer 산포(σ)"])
        self.cmb_point.setToolTip(
            "점 하나를 무엇으로 찍을지 고릅니다.\n"
            "측정점 그대로(site) / wafer별 평균·중앙값·표준편차")
        self.cmb_point.setCurrentIndex(
            POINT_MODES.index(state.explore.mode)
            if state.explore.mode in POINT_MODES else 0)
        on_combo(self.cmb_point, self._point_changed)
        card.body.addWidget(row("점", self.cmb_point, stretch_at=1))
        self.lbl_hint = QLabel()
        self.lbl_hint.setObjectName("hint")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setMinimumHeight(74)
        card.body.addWidget(self.lbl_hint)
        self._sync_hint()
        card.body.addSpacing(4)
        return card

    # ── 스케일 · 범위 카드 (§5.2) ────────────────────────────
    def _scale_card(self) -> Card:
        card = Card("스케일 · 범위")
        card.setObjectName("probeSection")
        self.cmb_scale: dict[str, QComboBox] = {}
        self.ed_range: dict[str, QLineEdit] = {}
        for axis in ("x", "y"):
            cmb = QComboBox()
            cmb.addItems(["자동", "log", "선형"])
            cmb.setCurrentIndex(
                ("auto", "log", "linear").index(
                    getattr(self.state.explore, f"log{axis}_mode", "auto")))
            on_combo(cmb, self._scale_changed, axis)
            self.cmb_scale[axis] = cmb
            card.body.addWidget(row(axis.upper(), cmb, stretch_at=1))

        self.chk_manual = QCheckBox("범위 직접 지정")
        self.chk_manual.setChecked(self.state.explore.range_mode == "manual")
        self.chk_manual.toggled.connect(self._manual_toggled)
        card.body.addWidget(self.chk_manual)
        for axis in ("x", "y"):
            lo, hi = QLineEdit(), QLineEdit()
            for ed, name in ((lo, f"{axis}min"), (hi, f"{axis}max")):
                ed.setPlaceholderText(name)
                ed.editingFinished.connect(self._range_edited)
                self.ed_range[name] = ed
            h = QHBoxLayout()
            # 기본 여백(9px)을 지우지 않으면 이 줄만 위의 스케일 콤보보다
            # 안쪽으로 들어가 카드 안에서 왼쪽 선이 두 개가 된다.
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QLabel(axis.upper()))
            h.addWidget(lo, 1)
            h.addWidget(hi, 1)
            w = QWidget()
            w.setLayout(h)
            card.body.addWidget(w)
        self._sync_range_inputs()
        return card

    def _scale_changed(self, axis: str) -> None:
        mode = ("auto", "log", "linear")[self.cmb_scale[axis].currentIndex()]
        setattr(self.state.explore, f"log{axis}_mode", mode)
        self.mark_stale()

    def _manual_toggled(self, on: bool) -> None:
        st = self.state
        st.explore.range_mode = "manual" if on else "auto"
        if on:
            # 켜는 순간 **지금 보이는 축 값**을 채워 준다 — 빈칸에서 시작하면
            # 사용자가 자릿수를 손으로 옮겨 적어야 한다(확정 사양).
            for axis, (lo, hi) in self._current_limits().items():
                st.explore.__dict__[f"{axis}min"] = lo
                st.explore.__dict__[f"{axis}max"] = hi
        self._sync_range_inputs()
        self.mark_stale()

    def _current_limits(self) -> dict[str, tuple[float, float]]:
        """캔버스가 **실제로 쓴** 축 범위. 규칙을 두 곳에 두지 않기 위해
        여기서 다시 계산하지 않고 그려진 결과를 읽는다."""
        axes = self.canvas.figure.axes
        if not axes:
            return {}
        return {"x": axes[0].get_xlim(), "y": axes[0].get_ylim()}

    def _sync_range_inputs(self) -> None:
        """상태 → 입력칸. 시그널을 막아 재귀를 피한다."""
        st = self.state.explore
        for name, ed in self.ed_range.items():
            v = getattr(st, name, None)
            ed.blockSignals(True)
            ed.setText("" if v is None else f"{v:g}")
            ed.setEnabled(st.range_mode == "manual")
            ed.blockSignals(False)

    def _range_edited(self) -> None:
        st = self.state.explore
        for name, ed in self.ed_range.items():
            txt = ed.text().strip()
            try:
                setattr(st, name, float(txt) if txt else None)
            except ValueError:
                ed.setText("")           # 숫자가 아니면 비운다(자동으로 되돌림)
                setattr(st, name, None)
        self.mark_stale()

    # ── 그룹 ─────────────────────────────────────────────────
    # 그룹 스타일 카드는 여기 없다. 보이기·색·심볼·REF·편집은 인스펙터 공용
    # `[그룹]` 섹션 하나가 갖는다(`ui/widgets/group_section.py`, 설계 §1 규칙 2) —
    # 예전에는 탐색과 리포트에 한 벌씩 있어 같은 개념이 두 화면에 복제됐다.
    def _groups_changed(self) -> None:
        """그룹 토글·편집은 즉시 반영(확정 §3). 목록 갱신은 카드가 스스로 한다."""
        self.refresh_if_visible()

    # ── 지연 계산 (규약은 tabs/common.StaleMixin) ────────────
    def _on_exclusion(self) -> None:
        # 이 화면에서 찍은 제외는 바로 반영, 아니면 표시만
        if self.isVisible():
            self.redraw()
        else:
            self.mark_stale()

    def _axes_changed(self) -> None:
        st = self.state
        st.explore.x = self.ed_x.text()
        st.explore.y = self.ed_y.text()
        self._sync_type()
        self.mark_stale()

    def _point_changed(self) -> None:
        """점 표시 레벨(site/avg/med/std) — 템플릿의 Mode 열과 같은 값이다."""
        i = self.cmb_point.currentIndex()
        self.state.explore.mode = POINT_MODES[i] if 0 <= i < len(POINT_MODES) \
            else "site"
        self.mark_stale()

    # ── plot 종류 ────────────────────────────────────────────
    def _x_items(self) -> list[str]:
        """X 자동완성 — **종류에 따라 뜻이 다르다**.

        boxplot의 X는 item이 아니라 나눌 기준이다(`model/categories`). 여기에
        item 목록을 띄우면 사용자가 item을 골라 놓고 상자가 왜 수천 개인지
        묻게 된다. 종류를 바꾼 순간 후보도 바뀌어야 한다.
        """
        from etreport.model import categories as cat
        st = self.state
        if st.explore.type == "box":
            return cat.choices(st.data, st.track_columns + st.met_columns)
        if st.explore.type == "trend":
            return list(GEOM_COLUMNS)
        return self._y_items()

    def _y_items(self) -> list[str]:
        """Y 자동완성 — 리포메터 ALIAS(없으면 item 컬럼) + 붙인 계측 열(§1)."""
        return self.state.value_columns()

    def _type_changed(self) -> None:
        """종류를 고르면 **X도 그 종류가 읽을 수 있는 값으로** 바꿔 준다.

        X는 종류마다 뜻이 다르다(item ↔ 범주 ↔ 기하). 종류만 바꾸고 X를 그대로
        두면 그릴 수 없는 조합이 되어 빈 그림이 나오고, `_sync_type`이 종류를
        되돌려 놓아 "골랐는데 안 바뀐다"가 된다. 지금 X가 그 종류에 맞지 않을
        때만 손댄다 — 이미 맞으면 사용자가 적어 둔 값을 건드리지 않는다.
        """
        from etreport.model import categories as cat
        st = self.state
        typ = self.cmb_type.currentData() or "scatter"
        x = st.explore.x.strip()
        if typ == "trend" and x not in GEOM_COLUMNS:
            st.explore.x = GEOM_COLUMNS[0]
        elif typ == "box" and not cat.is_category(x, st.data):
            st.explore.x = cat.LOT_WAFER
        elif typ == "scatter" and (x in GEOM_COLUMNS
                                   or cat.is_category(x, st.data)):
            items = self._y_items()
            st.explore.x = items[0] if items else ""
        st.explore.type = typ
        self.ed_x.setText(st.explore.x)
        self._sync_hint()
        self.mark_stale()

    def _sync_hint(self) -> None:
        """종류별 안내 — X가 무엇인지가 종류마다 달라서 한 문장으로 못 적는다."""
        common = ("축 범위 자동 · 규격 ∪ 데이터 × 1.2\n"
                  "로그는 item 이름 규칙으로 자동")
        text = {
            "scatter": "쉼표로 여러 xy쌍 → 한 그림에 겹칩니다\n"
                       "X·Y 모두 item(리포메터 ALIAS)입니다\n" + common,
            "box": "X는 **나눌 기준**입니다 — lot+wafer · 그룹 · 온도 ·\n"
                   "step · fab tracking에서 뽑은 컬럼\n"
                   "Y는 item(쉼표로 여러 개)\n" + common,
            "trend": "X는 W 또는 L (리포메터의 WIDTH·LENGTH)\n"
                     "Y에 적은 item들이 기하값 위에 늘어섭니다\n" + common,
        }.get(self.state.explore.type, common)
        self.lbl_hint.setText(text.replace("**", ""))

    def _sync_type(self) -> None:
        """콤보와 상태를 맞춘다. X가 기하(W·L)면 trend로 **넘어가 준다**.

        탐색 탭은 템플릿의 Type 열이 없어서 예전에는 입력만 보고 판정했다. 이제
        종류를 직접 고르지만, X에 W/L을 적는 손버릇은 그대로 살려 둔다 — 그렇게
        적었는데 산점도로 그려 빈 그림이 나오는 것이 예전 버그였다.
        """
        st = self.state
        x = st.explore.x.strip()
        if x in GEOM_COLUMNS:
            st.explore.type = "trend"
        elif st.explore.type == "trend":
            # trend는 X가 W·L일 때만 뜻이 있다. X를 item으로 바꿨는데 trend로
            # 남아 있으면 빈 그림이 나온다 — 산점도로 되돌린다.
            st.explore.type = "scatter"
        if st.explore.type not in PLOT_TYPES:
            st.explore.type = "scatter"
        idx = PLOT_TYPES.index(st.explore.type)
        if self.cmb_type.currentIndex() != idx:
            self.cmb_type.blockSignals(True)
            self.cmb_type.setCurrentIndex(idx)
            self.cmb_type.blockSignals(False)
            self._sync_hint()

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
        """[그리기] — 그리는 동안 버튼을 잠그고 대기 커서를 띄운다.

        **워커 스레드 렌더는 쓰지 않는다**: matplotlib은 폰트·텍스트 메트릭
        캐시가 스레드 안전하지 않아 QThread에서 그리면 프로세스가 abort한다
        (`get_text_width_height_descent`에서 죽는 것을 확인). 그래서 렌더는
        UI 스레드에 두고 진행 표시만 붙인다.
        """
        st = self.state
        self.mark_fresh()
        if st.data is None:
            # 문구는 `ui/guidance` 하나에서 온다 — 빈 상태와 가이드가 다른 말을
            # 하면 사용자가 둘 중 무엇을 믿어야 할지 모른다(설계 §5).
            from etreport.ui import guidance
            self.empty.setText(guidance.empty_message(st, "plot"))
            self.body.setCurrentWidget(self.empty)
            self.lbl_info.setText("데이터 없음")
            self.canvas.figure.clear()
            self.canvas.draw_idle()
            return
        self.body.setCurrentWidget(self.canvas)
        if not st.explore.x and st.data.width > len(RESERVED):
            items = item_columns(st.data)
            st.explore.x, st.explore.y = items[0], items[min(1, len(items) - 1)]
        for ed, val in ((self.ed_x, st.explore.x), (self.ed_y, st.explore.y)):
            if not ed.hasFocus() and ed.text() != val:
                ed.setText(val)
        self._sync_type()
        n = st.data.height
        self.btn_draw.setEnabled(False)
        self.lbl_info.setText("그리는 중…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.canvas.draw_spec(st.explore)
        finally:
            QApplication.restoreOverrideCursor()
            self.btn_draw.setEnabled(True)
        self.lbl_info.setText(f"{n - len(st.excluded):,} / {n:,} 포인트")
