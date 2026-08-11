"""리포메터 창 — 파일/시트를 골라 바로 바꿔 볼 수 있게.

한 파일에 공정별·버전별 시트를 여러 개 두고 쓰는 경우가 많아서, 파일을
다시 여는 대신 시트만 바꿔 끼우게 한다. 시트 목록·읽기는 xlio 캐시를
거치므로 두 번째부터는 Excel을 띄우지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from etreport.model.state import AppState


class ReformatterDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.path: str = getattr(state, "rf_path", "") or ""
        self.sheet: str | int = getattr(state, "rf_sheet", 0)
        self._loaded = None                 # 확인 눌렀을 때 넘길 Reformatter

        self.setWindowTitle("리포메터")
        self.resize(900, 660)
        v = QVBoxLayout(self)

        # ── 파일 / 시트 ──────────────────────────────────────
        top = QHBoxLayout()
        top.addWidget(QLabel("파일"))
        self.lbl_file = QLabel(self.path or "(선택 안 됨)")
        self.lbl_file.setObjectName("hint")
        top.addWidget(self.lbl_file, 1)
        b = QPushButton("파일 열기")
        b.setProperty("ghost", True)
        b.clicked.connect(self._pick_file)
        top.addWidget(b)
        v.addLayout(top)

        row = QHBoxLayout()
        row.addWidget(QLabel("시트"))
        self.cmb_sheet = QComboBox()
        self.cmb_sheet.setMinimumWidth(240)
        self.cmb_sheet.currentTextChanged.connect(self._sheet_changed)
        row.addWidget(self.cmb_sheet)
        b2 = QPushButton("새로 읽기")
        b2.setProperty("ghost", True)
        b2.setToolTip("캐시를 무시하고 Excel에서 다시 읽습니다")
        b2.clicked.connect(lambda: self._load(force=True))
        row.addWidget(b2)
        row.addStretch(1)
        self.lbl_stat = QLabel()
        self.lbl_stat.setObjectName("hint")
        row.addWidget(self.lbl_stat)
        v.addLayout(row)

        # ── item 목록 ────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setObjectName("sumTable")
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.table, 1)

        # ── 검증 결과 ────────────────────────────────────────
        self.lbl_msg = QLabel()
        self.lbl_msg.setWordWrap(True)
        self.lbl_msg.setObjectName("warnBox")
        v.addWidget(self.lbl_msg)

        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        v.addWidget(self.bb)

        if self.path:
            self._fill_sheets()
        else:
            self._set_msg("리포메터 파일을 여세요", ok=False)
            self.bb.button(QDialogButtonBox.Ok).setEnabled(False)

    # ── 동작 ─────────────────────────────────────────────────
    def _pick_file(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "리포메터", "",
                                           "Excel (*.xlsx *.xlsm)")
        if p:
            self.path = p
            self.lbl_file.setText(p)
            self._fill_sheets()

    def _fill_sheets(self) -> None:
        try:
            from etreport.data.xlio import sheet_names
            names = sheet_names(self.path)
        except ImportError:
            self._set_msg("xlwings/Excel이 없는 환경입니다 — 사내 PC에서 여세요",
                          ok=False)
            return
        except Exception as e:                       # noqa: BLE001
            self._set_msg(f"파일을 열 수 없습니다: {e}", ok=False)
            return
        self.cmb_sheet.blockSignals(True)
        self.cmb_sheet.clear()
        self.cmb_sheet.addItems(names)
        if isinstance(self.sheet, str) and self.sheet in names:
            self.cmb_sheet.setCurrentText(self.sheet)
        self.cmb_sheet.blockSignals(False)
        self._load()

    def _sheet_changed(self, name: str) -> None:
        if name:
            self.sheet = name
            self._load()

    def _load(self, force: bool = False) -> None:
        if not self.path:
            return
        sheet = self.cmb_sheet.currentText() or 0
        try:
            from etreport.data import xlio
            if force:
                xlio.invalidate(self.path)
            from etreport.data.reformatter import load as rf_load
            rf = rf_load(self.path, sheet)
        except ImportError:
            self._set_msg("xlwings/Excel이 없는 환경입니다", ok=False)
            return
        except Exception as e:                       # noqa: BLE001
            self._set_msg(f"읽기 실패: {e}", ok=False)
            self._loaded = None
            self.bb.button(QDialogButtonBox.Ok).setEnabled(False)
            return

        self._loaded = rf
        self.sheet = sheet
        self._fill_table(rf)
        self.lbl_stat.setText(
            f"REAL {len(rf.reals())} · ADDP {len(rf.addps())}"
            f" · {Path(self.path).name}")

        if rf.errors:
            self._set_msg("구조 오류 — 이 시트는 쓸 수 없습니다:\n"
                          + "\n".join(rf.report_lines()[:6]), ok=False)
            self.bb.button(QDialogButtonBox.Ok).setEnabled(False)
        elif rf.warnings:
            head = "\n".join(f"{w.row}행 {w.alias}: {w.message}"
                             for w in rf.warnings[:6])
            more = f"\n… 외 {len(rf.warnings) - 6}건" if len(rf.warnings) > 6 else ""
            self._set_msg(f"{len(rf.warnings)}개 item을 제외하고 진행합니다\n"
                          + head + more, ok=None)
            self.bb.button(QDialogButtonBox.Ok).setEnabled(True)
        else:
            self._set_msg(f"검증 통과 — item {len(rf.rules)}개", ok=True)
            self.bb.button(QDialogButtonBox.Ok).setEnabled(True)

    def _fill_table(self, rf) -> None:
        cols = ["CATEGORY", "ITEMID", "ALIAS", "ABS", "SCALE",
                "ADDP FORM", "UNIT", "SPECLOW", "SPECHIGH", "TARGET"]
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(rf.rules))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.verticalHeader().setVisible(False)
        for r, rule in enumerate(rf.rules):
            vals = [rule.category, rule.itemid, rule.alias,
                    "Y" if rule.absolute else "",
                    f"{rule.scale:g}", rule.formula, rule.unit,
                    "" if rule.speclow is None else f"{rule.speclow:g}",
                    "" if rule.spechigh is None else f"{rule.spechigh:g}",
                    "" if rule.target is None else f"{rule.target:g}"]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(str(val))
                if c >= 7:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if rule.category == "ADDP":
                    it.setForeground(QColor("#0058b0"))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()

    def _set_msg(self, text: str, ok: bool | None) -> None:
        self.lbl_msg.setText(text)
        self.lbl_msg.setProperty(
            "level", "ok" if ok is True else "warn" if ok is None else "err")
        self.lbl_msg.style().unpolish(self.lbl_msg)
        self.lbl_msg.style().polish(self.lbl_msg)

    # ── 결과 ─────────────────────────────────────────────────
    def result_reformatter(self):
        return self._loaded
