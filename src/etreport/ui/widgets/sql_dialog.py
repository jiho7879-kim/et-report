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
#: 이 행 수를 넘으면 SBDF(메모리 경유) 저장 전에 한 번 묻는다
SBDF_WARN_ROWS = 2_000_000

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


def _sub(sql: str) -> str:
    """사용자 SQL을 감싸 쓸 수 있는 서브쿼리 형태로."""
    return f"SELECT * FROM (\n{sql}\n)"


def preview_query(db_path: str, sql: str,
                  rows: int = PREVIEW_ROWS) -> tuple[pl.DataFrame, int]:
    """(미리보기 프레임, 전체 행 수).

    **결과 전체를 파이썬으로 가져오지 않는다.** 예전에는 `con.execute(sql).pl()`로
    전부 실체화해서, 조건을 안 건 조회 하나에
    `Out of Memory Error: Arrow buffer failed to allocate`로 죽었다. 화면에
    필요한 것은 200행뿐이고 저장은 DuckDB가 파일로 직접 흘려보내므로
    (아래 `copy_to`) 전체를 메모리에 올릴 이유가 없다.
    """
    from etreport.data.loader import open_readonly
    con = open_readonly(db_path)
    try:
        head = con.execute(f"{_sub(sql)} LIMIT {int(rows)}").pl()
        got = con.execute(f"SELECT count(*) FROM (\n{sql}\n)").fetchone()
        return head, int(got[0]) if got else head.height
    finally:
        con.close()


def copy_to(db_path: str, sql: str, out: str, fmt: str) -> str:
    """조회 결과를 DuckDB가 **파일로 직접** 쓰게 한다 (메모리 경유 없음).

    fmt는 "csv" 또는 "parquet". CSV는 엑셀에서 한글이 깨지지 않도록 BOM을
    앞에 붙인다 — DuckDB가 다 쓴 뒤 3바이트만 앞에 이어 붙인다.
    """
    from etreport.data.loader import open_readonly
    opts = ("FORMAT CSV, HEADER" if fmt == "csv" else "FORMAT PARQUET")
    target = Path(out)
    tmp = target.with_name(target.name + ".part") if fmt == "csv" else target
    con = open_readonly(db_path)
    try:
        con.execute(f"COPY (\n{sql}\n) TO '{str(tmp).replace(chr(39), chr(39) * 2)}'"
                    f" ({opts})")
    finally:
        con.close()
    if fmt == "csv":
        with target.open("wb") as dst:
            dst.write(b"\xef\xbb\xbf")
            with tmp.open("rb") as src:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
        tmp.unlink(missing_ok=True)
    return out


class SqlExportDialog(QDialog):
    def __init__(self, db_path: str, table: str = "et_data", parent=None) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.table = table or "et_data"
        self.df: pl.DataFrame | None = None      # 미리보기(최대 PREVIEW_ROWS행)
        self.n_rows = 0                          # 조회 결과 전체 행 수

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

        note = QLabel(f"미리보기는 {PREVIEW_ROWS}행만 읽습니다 · "
                      "CSV·parquet 저장은 DuckDB가 파일로 바로 씁니다"
                      "(전체 결과 · 메모리 경유 없음) · DB는 읽기 전용으로 엽니다")
        note.setObjectName("hint")
        v.addWidget(note)

    # ── 실행 ─────────────────────────────────────────────────
    def _use_snippet(self, name: str) -> None:
        if name in SNIPPETS:
            self.editor.setPlainText(
                SNIPPETS[name].replace("et_data", self.table))

    def sql_text(self) -> str:
        return self.editor.toPlainText().strip().rstrip(";")

    def _run(self) -> None:
        """조회는 워커 스레드로 — 큰 DB에서는 수 초~수십 초 걸린다."""
        if not self.sql_text():
            return
        from etreport.ui.widgets.worker import run_in_background
        t0 = time.monotonic()
        run_in_background(self, "SQL 조회", lambda: preview_query(
            self.db_path, self.sql_text()),
            done=lambda res: self._run_done(res, t0))

    def _run_done(self, res, t0: float) -> None:
        self.df, self.n_rows = res
        self._show_result(t0)

    def _run_sync(self) -> None:
        """테스트·스크립트용 동기 실행 경로(진행 창 없이)."""
        if not self.sql_text():
            return
        t0 = time.monotonic()
        try:
            self.df, self.n_rows = preview_query(self.db_path, self.sql_text())
        except Exception as e:                       # noqa: BLE001
            self.df = None
            self.lbl_stat.setText("실행 실패")
            QMessageBox.critical(self, "SQL 오류", str(e))
            return
        self._show_result(t0)

    def _show_result(self, t0: float) -> None:
        """조회 결과 요약 + 미리보기 — 동기·비동기 경로가 함께 쓴다."""
        if self.df is None:
            self.lbl_stat.setText("실행 실패")
            return
        el = time.monotonic() - t0
        self.lbl_stat.setText(
            f"{self.n_rows:,}행 × {self.df.width}열 · {el:.2f}초"
            f" · 미리보기 {self.df.height}행")
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

    def _save_stream(self, title: str, default: str, filt: str,
                     fmt: str) -> None:
        """CSV·parquet 저장은 DuckDB가 파일로 직접 흘려보낸다.

        결과가 수천만 행이어도 메모리를 쓰지 않는다 — 예전에는 조회 결과를
        전부 파이썬에 올려 두었다가 저장해서, 저장 이전에 조회에서 이미
        메모리가 터졌다.
        """
        if not self._ready():
            return
        p, _ = QFileDialog.getSaveFileName(self, title, default, filt)
        if not p:
            return
        from etreport.ui.widgets.worker import run_in_background
        sql = self.sql_text()
        run_in_background(self, title,
                          lambda: copy_to(self.db_path, sql, p, fmt),
                          done=self._done)

    def _save_csv(self) -> None:
        self._save_stream("CSV 저장", "query.csv", "CSV (*.csv)", "csv")

    def _save_parquet(self) -> None:
        self._save_stream("parquet 저장", "query.parquet",
                          "Parquet (*.parquet)", "parquet")

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
        # SBDF만은 pandas 프레임이 필요해 전체를 메모리에 올린다 — 큰 결과는
        # 미리 알린다(여기서 막지는 않는다).
        if self.n_rows > SBDF_WARN_ROWS and QMessageBox.question(
                self, "SBDF 저장",
                f"{self.n_rows:,}행을 한 번에 메모리로 올립니다.\n"
                f"parquet으로 저장하면 메모리를 쓰지 않습니다.\n\n계속할까요?"
        ) != QMessageBox.Yes:
            return
        from etreport.data.loader import open_readonly
        try:
            con = open_readonly(self.db_path)
            try:
                full = con.execute(self.sql_text()).pl()
            finally:
                con.close()
            sbdf.export_data(full.to_pandas(), p)
        except Exception as e:                       # noqa: BLE001
            QMessageBox.critical(self, "SBDF 저장 실패", str(e))
            return
        self._done(p)

    def _done(self, path: str) -> None:
        QMessageBox.information(self, "저장 완료", f"{self.n_rows:,}행\n{path}")
