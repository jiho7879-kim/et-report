"""fab tracking 조회 창 — 조건 자동 채움 · SQL 직접 수정 · **이름 붙인 컬럼 뽑기**.

예전에는 lot만 물어보고 조회한 뒤 `process_id` 값이 그대로 표의 컬럼 머리글이
됐다. 그래서 (1) 컬럼 이름을 정할 자리가 없었고 (2) 한 step에서 recipe와 설비를
함께 보고 싶어도 컬럼을 하나밖에 만들 수 없었으며 (3) line·process·part를 매번
손으로 적어야 했다. 이 창이 그 셋을 한꺼번에 없앤다.

화면은 위에서 아래로 네 단이다.

  1. **조건** — lot·line·process·part·기간. 분석 중인 DuckDB에서 읽어 채운다
     (`data/lotcontext.py`). 기간 기본값은 ET tkout_time 기준 180일 이전부터.
  2. **SQL** — 위 조건으로 만든 문장이 그대로 보이고 **직접 고칠 수 있다**.
     조건을 바꾸면 [조건으로 다시 만들기]로 되돌린다(손으로 고친 것을 조용히
     덮지 않는다 — 고쳐 놓은 SQL이 사라지는 것이 가장 짜증나는 일이다).
  3. **가져올 컬럼** — 이름 · 원본 컬럼 · step을 줄마다 정한다. 조회하면
     조건이 갈리는 step으로 기본 제안이 채워지므로, 그대로 두면 지금까지와
     같은 결과가 나온다.
  4. **미리보기** — 실제로 붙을 `lot | wafer | <이름들…>` 표.

[적용]을 누르면 분석 프레임에 그 컬럼들이 붙고, 그때부터 boxplot x축·표 범주·
PPT에서 쓸 수 있다(`state.track_columns`).
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from etreport.data import fabtracking as ft
from etreport.data import lotcontext
from etreport.model.state import AppState
from etreport.ui.widgets.cards import GhostButton

log = logging.getLogger(__name__)

#: 컬럼 표의 열 순서.
COL_NAME, COL_SOURCE, COL_STEP = 0, 1, 2
ANY_STEP_LABEL = "(전체 step)"


class FabTrackDialog(QDialog):
    """조회 → 컬럼 정의 → 적용. 결과는 `columns`·`values`·`tracking`에 남는다."""

    def __init__(self, state: AppState, parent=None,
                 lots: list[str] | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("fab tracking 불러오기")
        self.resize(880, 760)

        self.tracking = None          # 조회 원본 (polars)
        self.values = None            # 붙일 값 (lot·wafer·이름들)
        self.columns: list[ft.TrackColumn] = []
        self.matrix = None            # 실험 조건으로도 쓸 수 있게 함께 만든다
        self._sql_touched = False     # 사용자가 SQL을 손으로 고쳤는가
        self._steps: list[str] = []

        picked = list(lots or state.lots_selected or state.lots_all)
        self.ctx = lotcontext.from_db(state.db_path, picked or None)

        v = QVBoxLayout(self)
        v.addWidget(self._cond_box(picked))
        v.addWidget(self._sql_box(), 1)
        v.addWidget(self._cols_box(), 1)
        v.addWidget(self._preview_box(), 1)

        self.lbl = QLabel(self.ctx.summary() if self.ctx.lots
                          else "분석 DB에서 조건을 읽지 못했습니다 — 직접 적어 주세요")
        self.lbl.setObjectName("hint")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)

        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.button(QDialogButtonBox.Ok).setText("적용")
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        v.addWidget(self.bb)

        self._rebuild_sql()
        self._restore_columns()
        self._sync_ok()

    # ── ① 조건 ───────────────────────────────────────────────
    def _cond_box(self, lots: list[str]) -> QWidget:
        box = QWidget()
        g = QVBoxLayout(box)
        g.setContentsMargins(0, 0, 0, 0)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("lot"))
        self.ed_lots = QLineEdit(", ".join(lots))
        self.ed_lots.setPlaceholderText("쉼표로 여러 개 · 비우면 기간으로만 좁힙니다")
        r1.addWidget(self.ed_lots, 3)
        r1.addWidget(QLabel("line"))
        self.ed_line = QLineEdit(self.ctx.line_id(ft.DEFAULT_LINE))
        r1.addWidget(self.ed_line, 1)
        g.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("process"))
        self.ed_process = QLineEdit(", ".join(self.ctx.process_ids))
        self.ed_process.setPlaceholderText("비우면 조건 없음")
        r2.addWidget(self.ed_process, 2)
        r2.addWidget(QLabel("part"))
        self.ed_part = QLineEdit(", ".join(self.ctx.part_ids))
        self.ed_part.setPlaceholderText("비우면 조건 없음")
        r2.addWidget(self.ed_part, 2)
        g.addLayout(r2)

        r3 = QHBoxLayout()
        lo, hi = self.ctx.date_range()
        r3.addWidget(QLabel("기간"))
        self.dt_from, self.dt_to = QDateEdit(), QDateEdit()
        for ed, d in ((self.dt_from, lo), (self.dt_to, hi)):
            ed.setCalendarPopup(True)
            ed.setDisplayFormat("yyyy-MM-dd")
            ed.setDate(QDate(d.year, d.month, d.day) if d
                       else QDate.currentDate())
        self.chk_dates = QCheckBox("기간 사용")
        self.chk_dates.setChecked(lo is not None)
        self.chk_dates.setToolTip(
            f"분석 중인 lot의 ET tkout_time 기준 "
            f"{lotcontext.DEFAULT_LOOKBACK_DAYS}일 이전부터가 기본값입니다.\n"
            "계측·tracking은 ET보다 앞선 공정에서 찍히므로 그 뒤를 볼 이유가 없습니다.")
        r3.addWidget(self.chk_dates)
        r3.addWidget(self.dt_from)
        r3.addWidget(QLabel("~"))
        r3.addWidget(self.dt_to)
        self.chk_ecn = QCheckBox("ECN 있는 lot만")
        self.chk_ecn.setChecked(True)
        self.chk_ecn.setToolTip("ein_ecn_no IS NOT NULL — split 실험이 걸린 lot")
        r3.addWidget(self.chk_ecn)
        r3.addStretch(1)
        b = GhostButton("조건으로 SQL 다시 만들기")
        b.clicked.connect(self._rebuild_sql)
        r3.addWidget(b)
        g.addLayout(r3)
        return box

    # ── ② SQL ────────────────────────────────────────────────
    def _sql_box(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel("조회 SQL — 직접 고쳐도 됩니다"))
        head.addStretch(1)
        self.btn_run = QPushButton("조회")
        self.btn_run.clicked.connect(self._run)
        head.addWidget(self.btn_run)
        v.addLayout(head)
        self.ed_sql = QPlainTextEdit()
        self.ed_sql.setMinimumHeight(110)
        self.ed_sql.textChanged.connect(self._sql_edited)
        v.addWidget(self.ed_sql, 1)
        return box

    def _sql_edited(self) -> None:
        self._sql_touched = True

    def build_sql(self) -> str:
        """조건 입력칸 → SQL. 테스트가 이 함수만 따로 부른다."""
        def split(text: str) -> list[str]:
            return [x.strip() for x in text.replace(";", ",").split(",")
                    if x.strip()]

        lo = hi = None
        if self.chk_dates.isChecked():
            lo = self.dt_from.date().toPython()
            hi = self.dt_to.date().toPython()
        return ft.build_tracking_sql(
            lots=split(self.ed_lots.text()) or None,
            line_id=self.ed_line.text().strip() or ft.DEFAULT_LINE,
            d_from=lo, d_to=hi,
            ecn_only=self.chk_ecn.isChecked(),
            process_ids=split(self.ed_process.text()) or None,
            part_ids=split(self.ed_part.text()) or None)

    def _rebuild_sql(self) -> None:
        self.ed_sql.blockSignals(True)
        self.ed_sql.setPlainText(self.build_sql())
        self.ed_sql.blockSignals(False)
        self._sql_touched = False

    # ── ③ 가져올 컬럼 ────────────────────────────────────────
    def _cols_box(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel("가져올 컬럼 — 이름은 자유롭게 (plot 축·표 범주로 쓰입니다)"))
        head.addStretch(1)
        for label, slot in (("＋ 줄 추가", self._add_row),
                            ("－ 줄 삭제", self._del_row),
                            ("자동 제안", self._suggest)):
            b = GhostButton(label)
            b.clicked.connect(slot)
            head.addWidget(b)
        v.addLayout(head)
        self.tbl_cols = QTableWidget(0, 3)
        self.tbl_cols.setHorizontalHeaderLabels(["이름", "원본 컬럼", "step"])
        self.tbl_cols.verticalHeader().setVisible(False)
        self.tbl_cols.setMinimumHeight(130)
        self.tbl_cols.itemChanged.connect(lambda _i: self._refresh_preview())
        v.addWidget(self.tbl_cols, 1)
        return box

    def _add_row(self, name: str = "", source: str = ft.AUTO_SOURCE,
                 step: str = "") -> int:
        r = self.tbl_cols.rowCount()
        self.tbl_cols.insertRow(r)
        self.tbl_cols.setItem(r, COL_NAME, QTableWidgetItem(name or f"track{r + 1}"))

        cmb_src = QComboBox()
        cmb_src.addItems(list(ft.SOURCE_CHOICES))
        cmb_src.setEditable(True)          # 목록에 없는 컬럼도 적을 수 있게
        cmb_src.setCurrentText(source)
        cmb_src.currentTextChanged.connect(lambda _t: self._refresh_preview())
        self.tbl_cols.setCellWidget(r, COL_SOURCE, cmb_src)

        cmb_step = QComboBox()
        cmb_step.addItems([ANY_STEP_LABEL, *self._steps])
        cmb_step.setEditable(True)
        cmb_step.setCurrentText(step or ANY_STEP_LABEL)
        cmb_step.currentTextChanged.connect(lambda _t: self._refresh_preview())
        self.tbl_cols.setCellWidget(r, COL_STEP, cmb_step)

        self.tbl_cols.resizeColumnsToContents()
        return r

    def _restore_columns(self) -> None:
        """지난번에 정한 컬럼 정의를 되살린다(§2).

        창을 닫았다 다시 열면 표가 비어 있어서, 조회는 됐는데 화면이 예전과
        다르게 채워졌다(자동 제안으로 덮였다). 정의를 상태에 남겨 두고 여기서
        되돌리면 "다시 열면 아무것도 안 나온다"가 사라진다.
        """
        for c in getattr(self.state, "track_specs", None) or []:
            self._add_row(c.name, c.source, c.step)

    def _del_row(self) -> None:
        r = self.tbl_cols.currentRow()
        if r < 0:
            r = self.tbl_cols.rowCount() - 1
        if r >= 0:
            self.tbl_cols.removeRow(r)
            self._refresh_preview()

    def _suggest(self) -> None:
        """조건이 갈리는 step으로 기본 제안 — 지금까지와 같은 결과가 되게."""
        if self.tracking is None:
            return
        self.tbl_cols.blockSignals(True)
        self.tbl_cols.setRowCount(0)
        for c in ft.suggest_columns(self.tracking):
            self._add_row(c.name, c.source, c.step)
        self.tbl_cols.blockSignals(False)
        self._refresh_preview()

    def spec_columns(self) -> list[ft.TrackColumn]:
        """표 → TrackColumn 목록. 이름은 여기서 한 번만 다듬는다(safe_name)."""
        out: list[ft.TrackColumn] = []
        taken: set[str] = set()
        for r in range(self.tbl_cols.rowCount()):
            item = self.tbl_cols.item(r, COL_NAME)
            src = self.tbl_cols.cellWidget(r, COL_SOURCE)
            stp = self.tbl_cols.cellWidget(r, COL_STEP)
            name = ft.safe_name(item.text() if item else "", taken)
            taken.add(name)
            step = (stp.currentText().strip() if stp else "")
            out.append(ft.TrackColumn(
                name=name,
                source=(src.currentText().strip() if src else ft.AUTO_SOURCE),
                step="" if step == ANY_STEP_LABEL else step))
        return out

    # ── ④ 미리보기 ───────────────────────────────────────────
    def _preview_box(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel("붙을 값 미리보기"))
        self.preview = QTableWidget()
        self.preview.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.preview, 1)
        return box

    def _refresh_preview(self) -> None:
        if self.tracking is None:
            return
        self.columns = self.spec_columns()
        try:
            self.values = ft.derive(self.tracking, self.columns)
        except Exception as e:               # noqa: BLE001 — 편집 중일 뿐이다
            self.lbl.setText(f"아직 만들 수 없습니다: {e}")
            self._sync_ok()
            return
        df = self.values
        cols = list(df.columns)
        self.preview.setColumnCount(len(cols))
        self.preview.setRowCount(min(df.height, 60))
        self.preview.setHorizontalHeaderLabels(cols)
        self.preview.verticalHeader().setVisible(False)
        for r, rec in enumerate(df.head(60).iter_rows(named=True)):
            for c, col in enumerate(cols):
                val = rec.get(col)
                self.preview.setItem(
                    r, c, QTableWidgetItem("" if val is None else str(val)))
        self.preview.resizeColumnsToContents()
        made = [c for c in cols if c not in ("lot", "wafer")]
        self.lbl.setText(
            f"{ft.summarize(self.tracking)} · 붙을 컬럼 {len(made)}개: "
            f"{', '.join(made) or '(없음)'}"
            + (f"  (미리보기 60/{df.height}행)" if df.height > 60 else ""))
        self._sync_ok()

    # ── 조회 ─────────────────────────────────────────────────
    def _run(self) -> None:
        """조회는 사내망 왕복이라 수 초씩 걸린다 — 진행 창을 띄우고 워커에서."""
        from etreport.ui.widgets.worker import bdq_call, run_in_background
        sql = self.ed_sql.toPlainText().strip()
        if not sql:
            self.lbl.setText("SQL이 비어 있습니다")
            return
        self.btn_run.setEnabled(False)
        self.lbl.setText("fab tracking 조회 중…")
        run_in_background(self, "fab tracking 조회",
                          bdq_call(lambda: ft.fetch(sql)),
                          done=self._done, always=self._unlock)

    def _unlock(self) -> None:
        self.btn_run.setEnabled(True)

    def _done(self, df) -> None:
        self.tracking = df
        if df is None or df.is_empty():
            self.lbl.setText("조회 결과가 0행입니다 — 조건(lot·기간·process)을 "
                             "넓혀 보세요")
            self._sync_ok()
            return
        try:
            self._steps = sorted({str(s) for s in
                                  ft.wafer_conditions(df)["step_id"].to_list()
                                  if s})
            # 실험 조건(SplitMatrix)도 같은 조회에서 만들어 둔다 — [실험 조건]
            # 창이 이 창을 열었을 때 조회를 두 번 하지 않게.
            self.matrix = ft.to_split_matrix(df)
        except Exception as e:      # 화면에 적어야 원인을 안다
            # 여기서 예외가 새면 화면은 **아무 말 없이 비어 있다**(슬롯 안이라
            # 호출한 쪽이 못 받는다). 조회는 됐는데 표가 안 채워지던 자리다.
            log.exception("fab tracking 결과 정리 실패")
            self.lbl.setText(f"조회는 됐지만 결과를 읽지 못했습니다: {e}")
            self._sync_ok()
            return
        if self.tbl_cols.rowCount() == 0:
            self._suggest()
        else:
            self._refill_steps()
            self._refresh_preview()

    def _refill_steps(self) -> None:
        """조회 결과의 step 목록을 콤보에 채운다(고른 값은 유지)."""
        for r in range(self.tbl_cols.rowCount()):
            cmb = self.tbl_cols.cellWidget(r, COL_STEP)
            if cmb is None:
                continue
            cur = cmb.currentText()
            cmb.blockSignals(True)
            cmb.clear()
            cmb.addItems([ANY_STEP_LABEL, *self._steps])
            cmb.setCurrentText(cur)
            cmb.blockSignals(False)

    def _sync_ok(self) -> None:
        ok = self.values is not None and not self.values.is_empty()
        self.bb.button(QDialogButtonBox.Ok).setEnabled(bool(ok))

    # ── 적용 ─────────────────────────────────────────────────
    def apply_to_state(self) -> list[str]:
        """분석 프레임에 붙인다. 붙은 컬럼 이름들을 반환.

        [적용]을 눌렀을 때만 부른다 — 미리보기만 보고 닫는 흐름에서 데이터가
        조용히 바뀌면 안 된다(지연 계산 규약).
        """
        st = self.state
        if self.values is None or st.data is None:
            return []
        st.data, names = ft.attach(st.data, self.values)
        st.track_columns = list(dict.fromkeys([*st.track_columns, *names]))
        st.track_frame = self.values
        st.track_specs = list(self.columns)      # 다시 열면 이 정의로 복원(§2)
        log.info("fab tracking 컬럼 %d개를 분석 프레임에 붙였습니다: %s",
                 len(names), ", ".join(names))
        return names
