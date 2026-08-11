"""SQL 조회 창 — 원하는 조건만 뽑아 CSV/SBDF로 저장.

적재된 DuckDB를 **읽기 전용**으로 열고 자유 SQL을 돌린다. 기본 적재 결과와
별개로, 필요한 조건만 뽑아 다른 도구(Spotfire 등)로 넘기기 위한 창이다.
SBDF는 사내에 라이브러리가 있을 때만 활성화되고, 없으면 CSV/parquet로 안내한다.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import polars as pl
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

log = logging.getLogger(__name__)

PREVIEW_ROWS = 200

SNIPPETS = {
    "전체": "SELECT * FROM et_data",
    "lot 지정": "SELECT * FROM et_data\nWHERE root_lot_id IN ('PA123', 'PA124')",
    "기간": ("SELECT * FROM et_data\n"
             "WHERE tkout_time >= '2026-08-01'\n"
             "  AND tkout_time <  '2026-08-11'"),
    "wafer 평균": ("SELECT root_lot_id, wafer_id, count(*) AS n,\n"
                   "       avg(\"Idsat N SVT\") AS idsat_avg\n"
                   "FROM et_data\nGROUP BY 1, 2\nORDER BY 1, 2"),
    "item 목록": ("SELECT column_name FROM information_schema.columns\n"
                  "WHERE table_name = 'et_data'"),
}


class SqlExportDialog(QDialog):
    def __init__(self, db_path: str, table: str = "et_data", parent=None) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.table = table or "et_data"
        self.df: pl.DataFrame | None = None

        self.setWindowTitle("SQL 조회 · 내보내기")
        self.resize(980, 720)
        v = QVBoxLayout(self)

        head = QHBoxLayout()
        head.addWidget(QLabel(f"DB   {Path(db_path).name}"))
        head.addWidget(QLabel(f"·   테이블   {self.table}"))
        head.addStretch(1)
        head.addWidget(QLabel("예시"))
        self.cmb_snip = QComboBox()
        self.cmb_snip.addItems(list(SNIPPETS))
        self.cmb_snip.currentTextChanged.connect(self._use_snippet)
        head.addWidget(self.cmb_snip)
        v.addLayout(head)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("sqlEditor")
        self.editor.setPlainText(SNIPPETS["전체"].replace("et_data", self.table))
        self.editor.setFixedHeight(170)
        v.addWidget(self.editor)

        bar = QHBoxLayout()
        b_run = QPushButton("실행")
        b_run.clicked.connect(self._run)
        bar.addWidget(b_run)
        for label, fn in (("CSV로 저장", self._save_csv),
                          ("SBDF로 저장", self._save_sbdf),
                          ("parquet로 저장", self._save_parquet)):
            b = QPushButton(label)
            b.setProperty("ghost", True)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        self.lbl_stat = QLabel("실행하면 결과가 여기 표시됩니다")
        self.lbl_stat.setObjectName("hint")
        bar.addWidget(self.lbl_stat)
        v.addLayout(bar)

        self.preview = QTableWidget()
        self.preview.setObjectName("sumTable")
        self.preview.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.preview, 1)

        note = QLabel(f"미리보기는 {PREVIEW_ROWS}행까지만 표시합니다 · "
                      "저장은 전체 결과가 들어갑니다 · DB는 읽기 전용으로 엽니다")
        note.setObjectName("hint")
        v.addWidget(note)

    # ── 실행 ─────────────────────────────────────────────────
    def _use_snippet(self, name: str) -> None:
        if name in SNIPPETS:
            self.editor.setPlainText(
                SNIPPETS[name].replace("et_data", self.table))

    def _run(self) -> None:
        sql = self.editor.toPlainText().strip().rstrip(";")
        if not sql:
            return
        from etreport.data.loader import open_readonly
        t0 = time.monotonic()
        try:
            con = open_readonly(self.db_path)
            try:
                self.df = con.execute(sql).pl()
            finally:
                con.close()
        except Exception as e:                       # noqa: BLE001
            self.df = None
            self.lbl_stat.setText("실행 실패")
            QMessageBox.critical(self, "SQL 오류", str(e))
            return
        el = time.monotonic() - t0
        self.lbl_stat.setText(
            f"{self.df.height:,}행 × {self.df.width}열 · {el:.2f}초")
        self._fill_preview()

    def _fill_preview(self) -> None:
        df = self.df
        if df is None:
            return
        head = df.head(PREVIEW_ROWS)
        self.preview.setColumnCount(df.width)
        self.preview.setRowCount(head.height)
        self.preview.setHorizontalHeaderLabels(df.columns)
        self.preview.verticalHeader().setVisible(False)
        for r, rec in enumerate(head.iter_rows()):
            for c, val in enumerate(rec):
                it = QTableWidgetItem("" if val is None else str(val))
                if isinstance(val, (int, float)):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.preview.setItem(r, c, it)
        self.preview.resizeColumnsToContents()

    # ── 저장 ─────────────────────────────────────────────────
    def _ready(self) -> bool:
        if self.df is None:
            QMessageBox.information(self, "저장", "먼저 [실행]으로 결과를 만드세요")
            return False
        return True

    def _save_csv(self) -> None:
        if not self._ready():
            return
        p, _ = QFileDialog.getSaveFileName(self, "CSV 저장", "query.csv",
                                           "CSV (*.csv)")
        if not p:
            return
        # 엑셀에서 한글이 깨지지 않도록 BOM만 먼저 쓰고, 본문은 polars가 파일에
        # 직접 스트리밍한다 (예전처럼 CSV 전체를 문자열로 만들면 큰 결과에서
        # 메모리를 두 배로 쓴다).
        with Path(p).open("wb") as f:
            f.write(b"\xef\xbb\xbf")
            self.df.write_csv(f)
        self._done(p)

    def _save_parquet(self) -> None:
        if not self._ready():
            return
        p, _ = QFileDialog.getSaveFileName(self, "parquet 저장", "query.parquet",
                                           "Parquet (*.parquet)")
        if p:
            self.df.write_parquet(p)
            self._done(p)

    def _save_sbdf(self) -> None:
        if not self._ready():
            return
        try:
            import sbdf  # 사내 라이브러리
        except ImportError:
            QMessageBox.information(
                self, "SBDF",
                "sbdf 라이브러리를 찾을 수 없습니다.\n\n"
                "Spotfire로 넘기실 거라면 CSV 또는 parquet으로 저장한 뒤\n"
                "Spotfire에서 불러오셔도 동일한 결과가 됩니다.")
            return
        p, _ = QFileDialog.getSaveFileName(self, "SBDF 저장", "query.sbdf",
                                           "SBDF (*.sbdf)")
        if not p:
            return
        try:
            sbdf.export_data(self.df.to_pandas(), p)
        except Exception as e:                       # noqa: BLE001
            QMessageBox.critical(self, "SBDF 저장 실패", str(e))
            return
        self._done(p)

    def _done(self, path: str) -> None:
        n = self.df.height if self.df is not None else 0
        QMessageBox.information(self, "저장 완료", f"{n:,}행\n{path}")
