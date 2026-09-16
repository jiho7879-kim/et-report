"""데이터 워크스페이스 — 프리셋 · 대상 · 기간 · 조건 · 실행."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from etreport.config.catalog import Catalog
from etreport.config.settings import Condition, ExtractPreset, Settings
from etreport.data.querybuilder import (
    ConditionError,
    build_extract_sql,
    build_item_probe_sql,
    build_preview_sql,
)
from etreport.data.reformatter import load as rf_load
from etreport.model.state import AppState, StateBus
from etreport.ui.tabs.common import detach
from etreport.ui.widgets.autocomplete import AutoCompleteEdit
from etreport.ui.widgets.cards import GhostButton, row
from etreport.ui.widgets.item_check_dialog import ItemCheckDialog

log = logging.getLogger(__name__)

# 폭이 이만큼 되면 카드를 2열로 편다(1500px 창에서 좌우가 통째로 비지 않게)
WIDE_BREAKPOINT = 1180
WIDE_MAX = 1360
NARROW_MAX = 900

WAIT_TEXT = "준비됨"
LOG_PLACEHOLDER = ("여기에 단계별 진행이 남습니다.\n"
                   "리포메터 → 추출 → 리포메팅 → 적재 순서로 진행하고, "
                   "각 단계의 소요 시간도 함께 적힙니다.")


def _chrome_section(title: str, sub: str = "") -> QWidget:
    """흰 카드 대신 잉크 크롬 위에 놓이는 조작면 묶음.

    데이터 화면에는 측정면(흰 종이)이 없으므로 Card(흰 카드) 대신 크롬
    섹션을 쓴다. `#sectionGroup` 패널(INK_2 + hairline, 설계 §5)로 감싸 네
    섹션(설정·대상·기간·조회 조건)이 구분되게 한다. 제목은 #sectionLabel로
    #sectionHead에 담아 전역 라벨의 위 여백을 지운다(패널 안에서는 묶음
    경계를 패널 테두리가 대신한다). 내용은 `.body`에 채운다.
    """
    w = QWidget()
    w.setObjectName("sectionGroup")
    v = QVBoxLayout(w)
    v.setContentsMargins(16, 12, 16, 14)
    v.setSpacing(8)
    head = QWidget()
    head.setObjectName("sectionHead")
    hl = QHBoxLayout(head)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(6)
    lab = QLabel(title)
    lab.setObjectName("sectionLabel")
    hl.addWidget(lab)
    if sub:
        s = QLabel(sub)
        s.setObjectName("hint")
        hl.addWidget(s)
    hl.addStretch(1)
    v.addWidget(head)
    w.body = v
    return w


class _ExtractThread(QThread):
    """추출 → 리포메팅 → 적재를 **워커 스레드에서** 돌린다.

    실제 순서와 계산은 전부 `data/pipeline.run()`이 갖는다. 여기 남은 일은
    그 콜백을 Qt 시그널로 옮기는 것뿐이다 — 파이프라인이 화면 안에 들어 있으면
    예약 실행(§13)이 같은 코드를 한 벌 더 쓰게 되고, 두 경로가 조용히 갈린다.
    """

    log = Signal(str)                 # 한 줄 로그
    step = Signal(str, int, int)      # 단계명, 완료, 전체
    finished_ok = Signal(int, str)    # 적재 행수, 소요시간
    failed = Signal(str)

    def __init__(self, preset, d_from, d_to, catalog) -> None:
        super().__init__()
        self.p, self.d_from, self.d_to, self.catalog = preset, d_from, d_to, catalog

    def run(self) -> None:
        from etreport.data import pipeline
        try:
            res = pipeline.run(
                self.p, self.d_from, self.d_to, self.catalog,
                on_log=self.log.emit,
                on_step=lambda label, done, total: self.step.emit(
                    label, done, total),
                should_stop=self.isInterruptionRequested)
            self.finished_ok.emit(res.rows, f"{res.seconds:.1f}초")
        except Exception as e:                      # noqa: BLE001 → UI로
            self.log.emit(f"실패: {e}")
            self.failed.emit(str(e))


class ConditionRow(QWidget):
    """컬럼 자동완성 + 타입 배지 + 타입별 값 힌트."""

    changed = Signal()
    removed = Signal(object)

    def __init__(self, cond: Condition, catalog: Catalog, parent=None) -> None:
        super().__init__(parent)
        self.cond, self.catalog = cond, catalog
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)

        self.ed_col = AutoCompleteEdit(
            lambda: [c.name for c in catalog.condition_columns()], comma=False)
        self.ed_col.setText(cond.col)
        self.ed_col.setFixedWidth(160)
        self.ed_col.setEnabled(not cond.required)
        self.ed_col.editingFinished.connect(self._col_changed)
        h.addWidget(self.ed_col)

        self.badge = QLabel()
        self.badge.setObjectName("typeBadge")
        self.badge.setFixedWidth(76)
        self.badge.setAlignment(Qt.AlignCenter)
        h.addWidget(self.badge)

        self.ed_val = QLineEdit(cond.val)
        self.ed_val.textEdited.connect(self._val_changed)
        h.addWidget(self.ed_val, 1)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["일반", "정규식"])
        self.cmb_mode.setCurrentIndex(1 if cond.mode == "regexp" else 0)
        self.cmb_mode.setFixedWidth(88)
        self.cmb_mode.currentIndexChanged.connect(self._mode_changed)
        h.addWidget(self.cmb_mode)

        if not cond.required:
            b = QPushButton("✕")
            b.setFixedWidth(30)
            b.setProperty("ghost", True)
            b.clicked.connect(lambda: self.removed.emit(self))
            h.addWidget(b)
        else:
            h.addSpacing(30)
        self._sync()

    def _col_changed(self) -> None:
        self.cond.col = self.ed_col.text().strip()
        self._sync()
        self.changed.emit()

    def _val_changed(self, t: str) -> None:
        self.cond.val = t
        self.changed.emit()

    def _mode_changed(self, i: int) -> None:
        self.cond.mode = "regexp" if i else "auto"
        self._sync()                       # 힌트 문구도 모드에 맞춘다
        self.changed.emit()

    def _sync(self) -> None:
        info = self.catalog.get(self.cond.col)
        t = info.dtype if info else "?"
        self.badge.setText(t)
        num = bool(info and info.is_numeric)
        ts = bool(info and info.is_timestamp)
        self.badge.setProperty("kind", "num" if num else "ts" if ts else "str")
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)
        self.cmb_mode.setEnabled(not (num or ts))
        rx = self.cond.mode == "regexp" and not (num or ts)
        self.ed_val.setPlaceholderText(
            "2026-08-01 ~ 2026-08-10" if ts else
            ">=25   ·   25 85   ·   25~85   ·   !0" if num else
            "P040 L040 P049   (띄어쓰기 = OR, 자동으로 | 로 잇습니다)" if rx else
            "PA12*  PB201  !PA125")


class DataWorkspace(QWidget):
    loaded = Signal()

    def __init__(self, settings: Settings, catalog: Catalog,
                 state: AppState, bus: StateBus, parent=None) -> None:
        super().__init__(parent)
        self.settings, self.catalog, self.state, self.bus = \
            settings, catalog, state, bus
        if not settings.extract_presets:
            settings.extract_presets.append(ExtractPreset(name="기본"))

        # 데이터 화면에는 측정면(흰 종이)이 없다 — 조작면은 전부 크롬 하나다.
        # #console 규칙(잉크 배경·크롬 글자·크롬 입력·고스트 버튼)이 자식 전체에
        # 걸리도록 루트에 objectName을 붙인다(style.qss는 수정하지 않는다).
        self.setObjectName("console")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("dataScroll")
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)
        outer.setAlignment(Qt.AlignHCenter)
        scroll.setWidget(host)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(scroll, 1)

        # 카드는 격자에 담는다 — 창이 넓으면 2열, 좁으면 1열(_relayout).
        # 예전에는 880px 열 하나만 써서 1500px 창의 좌우가 통째로 비었다.
        self.col = QWidget()
        v = QGridLayout(self.col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setHorizontalSpacing(14)
        v.setVerticalSpacing(14)
        self.grid = v
        self._cols = 0
        outer.addWidget(self.col)
        outer.addStretch(1)

        # 프리셋 ----------------------------------------------
        pc = _chrome_section("설정", "목적마다 DB·리포메터·조건을 묶어 저장합니다")
        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems([p.name for p in settings.extract_presets])
        self.cmb_preset.currentIndexChanged.connect(self._load_preset)
        b_save = QPushButton("저장")
        b_save.clicked.connect(lambda: settings.save())
        b_new = GhostButton("새 설정")
        b_new.clicked.connect(self._save_as)
        b_del = QPushButton("삭제")
        b_del.setProperty("danger", True)
        b_del.clicked.connect(self._delete_preset)
        pc.body.addWidget(row(self.cmb_preset, b_save, b_new, b_del, stretch_at=0))
        self.card_preset = pc

        # 대상 ------------------------------------------------
        tc = _chrome_section("대상")
        self.lbl_db = QLabel()
        self.lbl_rfm = QLabel()
        b1 = self.btn_pick_db = GhostButton("변경")
        b1.clicked.connect(self._pick_db)
        b2 = self.btn_pick_rfm = GhostButton("변경")
        b2.clicked.connect(self._pick_rfm)
        tc.body.addWidget(row("DuckDB", self.lbl_db, None, b1, stretch_at=1))
        tc.body.addWidget(row("리포메터", self.lbl_rfm, None, b2, stretch_at=1))
        self.lbl_cat = QLabel()
        b3 = GhostButton("새로고침")
        b3.clicked.connect(self._refresh_catalog)
        tc.body.addWidget(row("컬럼 정보", self.lbl_cat, None, b3, stretch_at=1))
        self.card_target = tc

        # 기간 — **조회 조건 카드 안에** 넣는다. 예전에는 카드 하나를 통째로
        # 쓰면서 위 2/3가 비었고, 그만큼 좌우 칼럼 높이도 어긋났다(설계 §0 F).
        # 기간도 결국 조회를 좁히는 조건이라 한 카드에 묶이는 편이 읽기도 낫다.
        self.d_from = QDateEdit(QDate.currentDate().addDays(-7))
        self.d_to = QDateEdit(QDate.currentDate())
        for d in (self.d_from, self.d_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
            d.dateChanged.connect(self._refresh_sql)
        q3 = GhostButton("최근 3일")
        q3.clicked.connect(lambda: self._quick(3))
        q7 = GhostButton("최근 7일")
        q7.clicked.connect(lambda: self._quick(7))
        q30 = GhostButton("최근 30일")
        q30.clicked.connect(lambda: self._quick(30))
        q1y = GhostButton("최근 1년")
        q1y.clicked.connect(lambda: self._quick(365))
        self.row_period = row(self.d_from, "—", self.d_to, q3, q7, q30, q1y, None)

        # 조건 ------------------------------------------------
        cc = _chrome_section(
            "조회 조건", "기간(tkout_time)은 파티션 컬럼이라 좁을수록 빠릅니다")
        lab_period = QLabel("기간")
        lab_period.setObjectName("sectionLabel")
        cc.body.addWidget(lab_period)
        cc.body.addWidget(self.row_period)
        lab_cond = QLabel("조건 — line_id는 필수")
        lab_cond.setObjectName("sectionLabel")
        cc.body.addWidget(lab_cond)
        self.cond_host = QVBoxLayout()
        self.cond_host.setSpacing(6)
        cc.body.addLayout(self.cond_host)
        add = GhostButton("＋ 조건 추가")
        add.clicked.connect(self._add_cond)
        cc.body.addWidget(row(add, None))       # 버튼은 내용만큼만
        self.sql = QPlainTextEdit()
        self.sql.setReadOnly(True)
        self.sql.setObjectName("sqlBox")
        self.sql.setFixedHeight(120)
        lab_sql = QLabel("SQL 미리보기")
        lab_sql.setObjectName("sectionLabel")
        cc.body.addWidget(lab_sql)
        cc.body.addWidget(self.sql)
        b_item = GhostButton("item_id 확인")
        b_item.setToolTip("리포메터의 ITEMID와 실제 데이터의 item_id를 대조합니다.")
        b_item.clicked.connect(self._open_item_check)
        cc.body.addWidget(row(b_item, None))
        self.card_cond = cc

        # 실행 ------------------------------------------------
        # 버튼은 **동작만** 말한다. 진행 단계는 라벨과 진행 막대가 말한다 —
        # 예전에는 버튼 글자가 "추출 중"으로 바뀌어 한 요소가 두 일을 했다.
        self.btn_run = QPushButton("추출하고 적재")
        self.btn_run.setMinimumWidth(150)
        self.btn_run.setToolTip("리포메터의 item만 조회해 추출·리포메팅·적재까지 (F5)")
        self.btn_run.clicked.connect(self._run)
        self.btn_cancel = GhostButton("중지")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setToolTip("진행 중인 청크가 끝나면 멈춥니다 (Esc)")
        self.btn_cancel.clicked.connect(self._cancel)
        self.chk_csv = QCheckBox("완료 후 CSV 저장")
        self.chk_csv.setChecked(True)
        self.chk_sbdf = QCheckBox("완료 후 SBDF 저장")
        self.lbl_step = QLabel(WAIT_TEXT)
        self.lbl_step.setObjectName("hint")
        self.btn_schedule = GhostButton("예약 실행…")
        self.btn_schedule.setToolTip(
            "정해진 시각에 이 프리셋으로 추출·적재를 돌립니다.\n"
            "앱이 꺼져 있어도 Windows 작업 스케줄러가 실행합니다.")
        self.btn_schedule.clicked.connect(self._open_schedule)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("logBox")
        self.log_view.setMinimumHeight(120)
        self.log_view.setPlaceholderText(LOG_PLACEHOLDER)
        b_copy = GhostButton("로그 복사")
        b_copy.clicked.connect(self._copy_log)
        b_clear = GhostButton("지우기")
        b_clear.clicked.connect(lambda: self.log_view.clear())
        lab_log = QLabel("진행 로그")
        lab_log.setObjectName("sectionLabel")

        # 실행/로그는 스크롤과 무관하게 항상 하단에 보이도록 고정한다.
        # 하단 콘솔 밴드(#console) 하나에 버튼·진행·로그를 모으고,
        # 넓게 남은 공간은 로그 영역이 차지하게 한다.
        console = QWidget()
        console.setObjectName("console")
        # 위 카드 열(`outer`)과 **글자 하나까지 같은 규칙**으로 가운데를 잡는다.
        # 예전에는 stretch(1) : 10 : stretch(1)로 나눠서, 여유 폭이 최대 폭보다
        # 넓을 때 푸터만 30px 남짓 안쪽으로 들어와 카드 왼쪽 선이 어긋났다.
        cl = QVBoxLayout(console)
        cl.setContentsMargins(24, 12, 24, 14)
        cl.setAlignment(Qt.AlignHCenter)
        self.console_inner = QWidget()
        self.console_inner.setMaximumWidth(880)
        self.console_inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        il = QVBoxLayout(self.console_inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(8)
        # 분석 화면 액션바와 **같은 관용구**: 왼쪽 끝이 주 동작, 오른쪽 끝이
        # 결과·옵션. 탭이나 화면이 바뀌어도 누를 것을 눈으로 찾지 않게 한다.
        il.addWidget(row(self.btn_run, self.btn_cancel, None,
                         self.chk_csv, self.chk_sbdf, self.btn_schedule))
        # 진행 문구는 제 줄에 둔다 — 체크박스 옆에 붙이면 그 체크박스의 설명처럼 읽힌다
        il.addWidget(row(self.lbl_step, None))
        il.addWidget(self.bar)
        il.addWidget(row(lab_log, None, b_copy, b_clear))
        il.addWidget(self.log_view, 1)
        cl.addWidget(self.console_inner)
        lay.addWidget(console)

        self._rows: list[ConditionRow] = []
        self._rf_items: list[str] = []   # 리포메터 REAL itemid (미리보기/필터/대조용)
        self._relayout(1)
        self._build_shortcuts()
        self._load_preset(0)

    # ── 레이아웃 ─────────────────────────────────────────────
    def _relayout(self, cols: int) -> None:
        """카드를 1열/2열로 다시 배치한다. 폭이 바뀔 때만 부른다."""
        if cols == self._cols:
            return
        self._cols = cols
        g = self.grid
        cards = (self.card_preset, self.card_target, self.card_cond)
        for c in cards:
            g.removeWidget(c)
        g.setColumnStretch(1, 0)
        if cols == 2:
            # 왼쪽에 짧은 둘(설정·대상), 오른쪽에 긴 하나(조회 조건 + 기간).
            # 2×2로 놓으면 카드 높이가 제각각이라 오른쪽 아래가 통째로 비었다.
            g.addWidget(self.card_preset, 0, 0)
            g.addWidget(self.card_target, 1, 0)
            g.addWidget(self.card_cond, 0, 1, 2, 1)
            g.setColumnStretch(0, 1)
            g.setColumnStretch(1, 1)
            self.col.setMaximumWidth(WIDE_MAX)
            self.console_inner.setMaximumWidth(WIDE_MAX)
        else:
            for i, c in enumerate(cards):
                g.addWidget(c, i, 0)
            g.setColumnStretch(0, 1)
            self.col.setMaximumWidth(NARROW_MAX)
            self.console_inner.setMaximumWidth(NARROW_MAX)
        g.setRowStretch(2, 1)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout(2 if self.width() >= WIDE_BREAKPOINT else 1)

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence("F5"), self, activated=self._run)
        QShortcut(QKeySequence("Esc"), self, activated=self._cancel)

    # ── 프리셋 ───────────────────────────────────────────────
    def preset(self) -> ExtractPreset:
        return self.settings.extract_presets[self.cmb_preset.currentIndex()]

    def _load_preset(self, _i: int) -> None:
        p = self.preset()
        self.chk_csv.setChecked(p.save_csv)
        self.chk_sbdf.setChecked(p.save_sbdf)
        self.lbl_db.setText(p.db_path or "(미지정)")
        self.lbl_rfm.setText(p.reformatter_path or "(미지정)")
        while self.cond_host.count():
            it = self.cond_host.takeAt(0)
            if it.widget():
                detach(it.widget())
        self._rows = []
        for c in p.conditions:
            self._add_row(c)
        self._refresh_catalog_label()
        self._load_rf_items()
        self._refresh_sql()

    def _save_as(self) -> None:
        name, ok = QInputDialog.getText(self, "새 설정", "이름")
        if ok and name:
            import copy
            p = copy.deepcopy(self.preset())
            p.name = name
            self.settings.extract_presets.append(p)
            self.cmb_preset.addItem(name)
            self.cmb_preset.setCurrentIndex(self.cmb_preset.count() - 1)
            self.settings.save()

    def _delete_preset(self) -> None:
        """현재 프리셋을 지운다 — 되돌릴 수 없으므로 확인을 받는다."""
        if len(self.settings.extract_presets) <= 1:
            QMessageBox.information(self, "삭제", "설정이 하나뿐입니다")
            return
        name = self.preset().name
        if QMessageBox.question(self, "삭제", f"'{name}' 설정을 지울까요?") \
                != QMessageBox.Yes:
            return
        idx = self.cmb_preset.currentIndex()
        self.settings.extract_presets.pop(idx)
        self.settings.save()
        self.cmb_preset.blockSignals(True)
        self.cmb_preset.removeItem(idx)
        self.cmb_preset.blockSignals(False)
        self._load_preset(self.cmb_preset.currentIndex())

    # ── 조건 ─────────────────────────────────────────────────
    def _add_row(self, cond: Condition) -> None:
        r = ConditionRow(cond, self.catalog)
        r.changed.connect(self._refresh_sql)
        r.removed.connect(self._remove_row)
        self._rows.append(r)
        self.cond_host.addWidget(r)

    def _add_cond(self) -> None:
        used = {c.col for c in self.preset().conditions}
        nxt = next((c.name for c in self.catalog.condition_columns()
                    if c.name not in used), "device_id")
        c = Condition(nxt)
        self.preset().conditions.append(c)
        self._add_row(c)
        self._refresh_sql()

    def _remove_row(self, r: ConditionRow) -> None:
        self.preset().conditions.remove(r.cond)
        self._rows.remove(r)
        r.deleteLater()
        self._refresh_sql()

    def _quick(self, n: int) -> None:
        self.d_to.setDate(QDate.currentDate())
        self.d_from.setDate(QDate.currentDate().addDays(-n + 1))

    # ── 입력 가이드 (설계 §5) ────────────────────────────────
    def requirements(self) -> list:
        """판정은 `ui/guidance` 하나가 한다 — 분석 화면과 같은 규칙이다."""
        from etreport.ui import guidance
        line = next((r.cond.val for r in self._rows
                     if r.cond.col == "line_id"), "")
        days = self.d_from.date().daysTo(self.d_to.date()) + 1
        return guidance.data_requirements(self.preset(), line, days)

    def _refresh_guidance(self) -> None:
        """비어 있는 필수 입력에 표시를 단다(층 1). 채우면 조용히 사라진다."""
        by = {r.key: r for r in self.requirements()}
        targets = {"db": self.btn_pick_db, "rfm": self.btn_pick_rfm}
        line_row = next((r for r in self._rows if r.cond.col == "line_id"), None)
        if line_row is not None:
            targets["line"] = line_row.ed_val
        for key, w in targets.items():
            r = by.get(key)
            missing = bool(r) and not r.done and not r.optional
            if w.property("needs") != ("true" if missing else "false"):
                w.setProperty("needs", "true" if missing else "false")
                w.style().unpolish(w)
                w.style().polish(w)
            if missing and r is not None:
                w.setToolTip(r.how)

    def _refresh_sql(self) -> None:
        try:
            # 실제 목록은 실행할 때만 만든다(§10.10) — 여기서는 개수 주석만
            sql = build_preview_sql(
                self.preset().conditions, self.d_from.date().toPython(),
                self.d_to.date().toPython(), self.catalog,
                item_ids=self._rf_items)
            self.sql.setPlainText(sql)
        except ConditionError as e:
            self.sql.setPlainText(f"-- {e.col}: {e}")
        self._refresh_guidance()

    # ── 리포메터 item (필터/미리보기/대조) ───────────────────
    def _load_rf_items(self) -> None:
        """리포메터 REAL itemid 목록을 캐시 (xlsx 미설치 환경은 빈 목록)."""
        self._rf_items = []
        p = self.preset()
        if not p.reformatter_path:
            return
        try:
            rf = rf_load(p.reformatter_path, p.reformatter_sheet or 0)
        except Exception as e:            # noqa: BLE001 — xlwings 미설치 등
            log.warning("리포메터 item 목록 로드 실패(%s) — 필터 미적용", e)
            return
        self._rf_items = [r.itemid for r in rf.reals() if r.itemid]

    def _gather_actual_items(self) -> list[str] | None:
        """실제 item 목록 — 적재된 DuckDB 우선, 없으면 bdq 프로브."""
        p = self.preset()
        if p.db_path and Path(p.db_path).exists():
            try:
                # 읽기 전용 연결은 반드시 loader를 경유한다 — 설정이 하나여야
                # 같은 파일을 여러 곳에서 열 수 있다(loader.readonly_config)
                from etreport.data.loader import readonly_query
                with readonly_query(str(p.db_path)) as con:
                    cols = [r[0] for r in con.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='et_data'").fetchall()]
                keys = {"key_hash", "key", "lot", "wafer", "gid", "line_id",
                        "root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos",
                        "temperature", "step_id", "step_seq", "total_site_cnt",
                        "tkout_time"}
                return [c for c in cols if c.lower() not in keys]
            except Exception as e:            # noqa: BLE001
                log.warning("DB item 조회 실패(%s)", e)
        try:
            import bigdataquery as bdq  # 사내 패키지 — 지연 import
        except Exception:                       # noqa: BLE001 — 사내 PC 아님
            return None
        try:
            df = bdq.getData(build_item_probe_sql(
                p.conditions, self.d_from.date().toPython(),
                self.d_to.date().toPython(), self.catalog))
            import polars as pl
            return list(pl.from_pandas(df)["item_id"].unique().to_list())
        except Exception as e:            # noqa: BLE001
            log.warning("item 프로브 실패(%s)", e)
            return None

    def _open_item_check(self) -> None:
        actual = self._gather_actual_items()
        ItemCheckDialog(self._rf_items, actual, parent=self).exec()

    # ── 대상 ─────────────────────────────────────────────────
    def _pick_db(self) -> None:
        # 저장 다이얼로그 — 없는 파일명을 적으면 그대로 새 DB가 된다.
        # (기존 파일을 고르면 이어서 누적 적재. 덮어쓰기 확인은 뜨지만
        #  실제로 지우지 않고 열어서 씀)
        p, _ = QFileDialog.getSaveFileName(
            self, "DuckDB 선택 또는 새로 만들기", "et_data.duckdb",
            "DuckDB (*.duckdb)",
            options=QFileDialog.DontConfirmOverwrite)
        if p:
            if not p.lower().endswith(".duckdb"):
                p += ".duckdb"
            self.preset().db_path = p
            self.lbl_db.setText(p)

    def _pick_rfm(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self, "리포메터", "",
            "리포메터 (*.xlsx *.xlsm *.csv *.tsv);;Excel (*.xlsx *.xlsm);;CSV (*.csv *.tsv)")
        if not p:
            return
        from etreport.ui.analysis_ws import _pick_sheet
        sheet = _pick_sheet(self, p, "리포메터")
        if sheet is None:
            return
        self.preset().reformatter_path = p
        self.preset().reformatter_sheet = sheet if isinstance(sheet, str) else ""
        self.lbl_rfm.setText(p + (f"  [{sheet}]" if isinstance(sheet, str) else ""))
        self._load_rf_items()
        self._refresh_sql()

    def _refresh_catalog(self) -> None:
        try:
            self.catalog.refresh()
        except ModuleNotFoundError:
            QMessageBox.information(
                self, "컬럼 정보",
                "bigdataquery 미설치 — 캐시된 카탈로그를 사용합니다.\n"
                "사내 PC에서는 bdq.getColumnInfo('eds.f_et_test')를 재조회합니다.")
        self._refresh_catalog_label()

    def _refresh_catalog_label(self) -> None:
        self.lbl_cat.setText(
            f"{len(self.catalog.columns)}개 컬럼 · "
            f"{self.catalog.fetched_at or '캐시 없음'}")

    def _open_schedule(self) -> None:
        """예약 실행 창(§13).

        예약은 **저장된 프리셋**을 이름으로 찾아 돈다 — 화면에서 고쳐 놓고 저장하지
        않은 조건은 예약 실행에 반영되지 않는다. 그래서 여는 순간 저장한다.
        """
        from etreport.ui.widgets.schedule_dialog import ScheduleDialog
        p = self.preset()
        if not (p.db_path and p.reformatter_path):
            QMessageBox.information(
                self, "예약 실행",
                "DB와 리포메터를 먼저 지정하세요 — 예약은 저장된 프리셋으로 돕니다")
            return
        self.settings.save()          # 지금 화면의 조건이 예약에도 쓰이도록
        ScheduleDialog(p, self).exec()
        self.settings.save()          # 창에서 바꾼 예약 설정을 남긴다

    # ── 실행 ─────────────────────────────────────────────────
    def _run(self) -> None:
        p = self.preset()
        p.save_csv = self.chk_csv.isChecked()
        p.save_sbdf = self.chk_sbdf.isChecked()
        d_from, d_to = self.d_from.date().toPython(), self.d_to.date().toPython()
        if d_from > d_to:
            QMessageBox.warning(
                self, "기간 오류",
                f"시작일({d_from})이 종료일({d_to})보다 뒤입니다")
            return
        try:
            build_extract_sql(p.conditions, d_from, d_to, self.catalog,
                              item_ids=self._rf_items)
        except ConditionError as e:
            QMessageBox.warning(self, "조건 오류", f"{e.col}: {e}")
            return
        if not (p.db_path and p.reformatter_path):
            QMessageBox.warning(self, "설정 필요",
                                "DuckDB 파일과 리포메터를 먼저 지정하세요")
            return
        # 분석 화면이 같은 DB를 읽기 전용으로 붙잡고 있으면 적재(쓰기)가
        # "different configuration" 오류로 떨어진다. 프레임은 이미 메모리에
        # 있으므로 연결만 닫아도 분석 화면은 그대로 돌아간다.
        from etreport.data.loader import close_store
        close_store(self.state)
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.log_view.clear()
        self._append(f"── {datetime.now():%H:%M:%S} 시작 ──")
        self._th = _ExtractThread(p, d_from, d_to, self.catalog)
        self._th.log.connect(self._append)
        self._th.step.connect(self._on_step)
        self._th.finished_ok.connect(self._done)
        self._th.failed.connect(self._fail)
        self._th.start()

    def shutdown(self) -> None:
        """앱 종료 시 추출 스레드를 정리한다 (MainWindow.closeEvent가 부른다).

        그냥 두면 QThread가 실행 중인 채로 파괴되어 프로세스가 죽는다
        (QThread: Destroyed while thread is still running). 이 위젯은
        QStackedWidget 안에 있어 자신의 closeEvent는 오지 않으므로,
        창 쪽에서 명시적으로 불러 줘야 한다.
        """
        th = getattr(self, "_th", None)
        if th is None or not th.isRunning():
            return
        th.requestInterruption()
        self._append("종료 중 — 진행 중인 청크가 끝나면 멈춥니다")
        if not th.wait(30_000):                  # 청크 하나가 끝날 때까지
            th.terminate()
            th.wait(2_000)

    def closeEvent(self, e) -> None:             # 단독 창으로 띄웠을 때 대비
        self.shutdown()
        super().closeEvent(e)

    def _append(self, line: str) -> None:
        self.log_view.appendPlainText(f"{datetime.now():%H:%M:%S}  {line}")
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_step(self, name: str, done: int, total: int) -> None:
        text = f"{name}   {done}/{total}" if total else name
        self.lbl_step.setText(text)
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        else:
            self.bar.setRange(0, 0)          # 불확정 애니메이션
        # 다른 화면을 보고 있거나 창이 작업표시줄에 내려가 있어도 보이게
        self._note(text)

    def _cancel(self) -> None:
        if getattr(self, "_th", None) and self._th.isRunning():
            self._th.requestInterruption()
            self._append("중지 요청 — 진행 중인 청크가 끝나면 멈춥니다")
            self.btn_cancel.setEnabled(False)

    def _note(self, text: str) -> None:
        """진행 상황을 상단 상태 레일로 올린다(화면을 떠나 있어도 보이게)."""
        self.state.status_note = text
        self.bus.status_changed.emit()

    def _reset_run_ui(self) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)

    def _done(self, n: int, elapsed: str) -> None:
        self._reset_run_ui()
        msg = f"적재 완료 — {n:,}행 · {elapsed}"
        self.lbl_step.setText(msg)
        self._note(msg)
        self.loaded.emit()

    def _fail(self, msg: str) -> None:
        self._reset_run_ui()
        self.lbl_step.setText("실패 — 아래 로그를 확인하세요")
        self._note("추출 실패")
        QMessageBox.critical(self, "추출 실패", msg)

    def _copy_log(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.log_view.toPlainText())
