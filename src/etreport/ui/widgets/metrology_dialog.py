"""inline 계측 창 — step_id·item_id 목록을 받아 분석 데이터에 붙인다(기능 B).

흐름은 넷뿐이다.
  1. 엑셀에서 복사한 `step_id  item_id` 목록을 붙여넣는다
  2. [불러오기] — **분석 중인 lot에 한해** `fab.f_fab_wf_met`를 조회해
     미리보기만 보여 준다(분석 데이터에는 아직 붙이지 않는다)
  3. [분석에 활용] — 조회 결과를 (lot, wafer)로 분석 데이터에 붙인다.
     이 버튼을 누르기 전에는 **결과만 조회한 상태**다(요구 사항)
  4. [유의 인자 분석] — 계측 인자 × ET item을 훑어 top-k를 뽑는다.
     결과는 상태에 남아 [PPT 생성] 때 슬라이드로 나간다

지연 계산 규약을 지킨다 — 이 창에서도 버튼을 눌러야 계산한다.
"""
from __future__ import annotations

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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from etreport.data import lotcontext
from etreport.model.state import AppState
from etreport.ui.widgets.cards import GhostButton


def _default_line() -> str:
    from etreport.data.metrology import DEFAULT_LINE
    return DEFAULT_LINE


class MetrologyDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.met = None                     # 조회 원본(polars)
        self.setWindowTitle("inline 계측 불러오기")
        self.resize(760, 640)

        # 조회 조건 기본값은 분석 중인 DB에서 읽는다(fab tracking과 같은 규칙).
        self.ctx = lotcontext.from_db(state.db_path,
                                      list(state.lots_selected) or None)

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "엑셀에서 복사한 step_id · item_id 목록을 붙여넣으세요 "
            "(비우면 CD|THK|DEPTH|TIP|RCS 전체)"))
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText("step_id\titem_id\nM1\tCD_A\nM5\tTHK_B")
        self.paste.setMaximumHeight(110)
        v.addWidget(self.paste)

        cond = QHBoxLayout()
        cond.addWidget(QLabel("line"))
        self.ed_line = QLineEdit(self.ctx.line_id(_default_line()))
        self.ed_line.setMaximumWidth(90)
        cond.addWidget(self.ed_line)
        lo, hi = self.ctx.date_range()
        self.chk_dates = QCheckBox("기간")
        self.chk_dates.setChecked(lo is not None)
        self.chk_dates.setToolTip(
            f"분석 중인 lot의 ET tkout_time 기준 "
            f"{lotcontext.DEFAULT_LOOKBACK_DAYS}일 이전부터가 기본값입니다.\n"
            "계측은 ET보다 앞선 공정에서 찍히므로 그 뒤를 볼 이유가 없습니다.")
        cond.addWidget(self.chk_dates)
        self.dt_from, self.dt_to = QDateEdit(), QDateEdit()
        for ed, d in ((self.dt_from, lo), (self.dt_to, hi)):
            ed.setCalendarPopup(True)
            ed.setDisplayFormat("yyyy-MM-dd")
            ed.setDate(QDate(d.year, d.month, d.day) if d else QDate.currentDate())
        cond.addWidget(self.dt_from)
        cond.addWidget(QLabel("~"))
        cond.addWidget(self.dt_to)
        cond.addStretch(1)
        b_sql = GhostButton("조건으로 SQL 다시 만들기")
        b_sql.clicked.connect(self._rebuild_sql)
        cond.addWidget(b_sql)
        v.addLayout(cond)

        v.addWidget(QLabel("조회 SQL — 직접 고쳐도 됩니다"))
        self.ed_sql = QPlainTextEdit()
        self.ed_sql.setMaximumHeight(110)
        v.addWidget(self.ed_sql)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("level"))
        self.cmb_level = QComboBox()
        self.cmb_level.addItems(["wafer (Q2)", "site (요약 제외)"])
        bar.addWidget(self.cmb_level)
        b = QPushButton("불러오기")
        b.clicked.connect(self._load)
        bar.addWidget(b)
        self.btn_use = QPushButton("분석에 활용")
        self.btn_use.setEnabled(False)   # 조회 전에는 누를 수 없다
        self.btn_use.setToolTip(
            "조회한 계측값을 분석 데이터에 (lot, wafer)로 붙입니다.\n"
            "누르기 전에는 결과를 조회만 한 상태입니다 — 누른 뒤에\n"
            "탐색 X축 · 요약 표 · PPT에서 쓸 수 있습니다.")
        self.btn_use.clicked.connect(self._use)
        bar.addWidget(self.btn_use)
        bar.addSpacing(12)
        bar.addWidget(QLabel("top-k"))
        self.spin_k = QSpinBox()
        self.spin_k.setRange(1, 50)
        self.spin_k.setValue(10)
        bar.addWidget(self.spin_k)
        b2 = QPushButton("유의 인자 분석")
        b2.setToolTip("분석 대상 lot 안에서 계측 인자와 ET item의 상관·그룹 차이를\n"
                      "훑어 유의미한 순으로 뽑습니다. 결과는 PPT에 슬라이드로 나갑니다.")
        b2.clicked.connect(self._analyze)
        bar.addWidget(b2)
        bar.addStretch(1)
        v.addLayout(bar)

        self.lbl = QLabel("분석 중인 DB의 lot으로 조회합니다")
        self.lbl.setObjectName("hint")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.table, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)

        self._rebuild_sql()          # 창을 열자마자 무엇을 조회할지 보이게

    # ── 조회 ─────────────────────────────────────────────────
    def level(self) -> str:
        return "site" if self.cmb_level.currentIndex() == 1 else "wafer"

    def lots(self) -> list[str]:
        st = self.state
        if st.data is None or "lot" not in st.data.columns:
            return []
        return sorted(set(st.data["lot"]))

    def build_sql(self) -> str:
        """붙여넣은 목록 + 조건 → 조회 SQL. 테스트가 이 함수만 따로 부른다."""
        from etreport.data import metrology as mt
        lots = self.lots()
        if not lots:
            return ""
        pairs = mt.parse_pairs(self.paste.toPlainText())
        steps = sorted({s for s, _ in pairs}) or None
        items = sorted({i for _, i in pairs}) or None
        lo = hi = None
        if self.chk_dates.isChecked():
            lo = self.dt_from.date().toPython()
            hi = self.dt_to.date().toPython()
        return mt.build_met_sql(
            lots, steps=steps, items=items,
            line_id=self.ed_line.text().strip() or mt.DEFAULT_LINE,
            item_regex=None if items else mt.ITEM_REGEX,
            d_from=lo, d_to=hi)

    def _rebuild_sql(self) -> None:
        sql = self.build_sql()
        self.ed_sql.setPlainText(
            sql or "-- 분석 DB를 먼저 열어 주세요 (lot을 알 수 없습니다)")

    def _load(self) -> None:
        """조회는 사내망 왕복이라 몇 초씩 걸린다 — 진행 창을 띄우고 워커에서.

        **SQL 칸에 적힌 문장을 그대로 돌린다.** 비어 있으면 조건으로 만들어
        채워 넣는다 — 손으로 고쳐 둔 SQL을 조용히 덮지 않는다.
        """
        from etreport.data import metrology as mt
        from etreport.ui.widgets.worker import bdq_call, run_in_background
        lots = self.lots()
        if not lots:
            self.lbl.setText("먼저 [적용]으로 분석 DB를 여세요 — lot을 알 수 없습니다")
            return
        if not self.ed_sql.toPlainText().strip():
            self._rebuild_sql()
        sql = self.ed_sql.toPlainText().strip()
        self.lbl.setText("계측 조회 중…")
        run_in_background(self, "inline 계측 조회",
                          bdq_call(lambda: mt.fetch(sql)),
                          done=lambda df: self._load_done(df, len(lots)))

    def _load_done(self, met, n_lots: int) -> None:
        """조회 결과를 미리보기만 한다 — 데이터에는 [분석에 활용]으로 붙인다.

        UI 갱신은 여기서만. 이 함수는 `state.data`를 건드리지 않는다 — 사용자가
        결과를 먼저 보고 붙일지 말지 정해야 하기 때문이다(요구: 버튼을 누르지
        않으면 그냥 결과만 조회한 상태).
        """
        self.met = met
        self.btn_use.setEnabled(met is not None and not met.is_empty())
        wide = self._met_wide()
        names = ([c for c in wide.columns if c not in ("lot", "wafer")]
                 if wide is not None else [])
        self.lbl.setText(
            f"lot {n_lots}개 · 계측 {len(names)}개 조회 완료 — "
            f"[분석에 활용]을 누르면 분석 데이터에 붙습니다")
        self._show_met_preview()

    def _use(self) -> None:
        """[분석에 활용] — 조회 결과를 이 버튼으로 분석 데이터에 붙인다."""
        from etreport.data import metrology as mt
        st = self.state
        if st.data is None:
            self.lbl.setText("먼저 [적용]으로 분석 DB를 여세요")
            return
        if self.met is None:
            self.lbl.setText("먼저 [불러오기]로 계측값을 조회하세요")
            return
        # 조건을 고쳐 다시 뽑는 흐름이라 **같은 이름은 덮어쓴다**(§1).
        # 건너뛰면 고친 결과가 반영되지 않아 "활용이 안 먹는다"가 된다.
        wide = self._met_wide()
        dup = [c for c in (wide.columns if wide is not None else [])
               if c not in ("lot", "wafer") and c in st.data.columns]
        if dup:
            st.data = st.data.drop(dup)
        st.data, names = mt.attach(st.data, self.met, self.level())
        if not names:
            self.lbl.setText("붙일 새 계측 열이 없습니다 (같은 이름이 이미 붙어 있습니다)")
            self._show_met_preview()
            return
        st.met_columns = sorted({*st.met_columns, *names})
        # 원본을 남겨야 [적용]으로 DB를 다시 읽어도 계측 열이 살아남는다(§1)
        st.met_frame, st.met_level = self.met, self.level()
        self.lbl.setText(
            f"분석에 활용 — 계측 {len(names)}개 붙임 ({self.level()} level) — "
            f"탐색 X축과 요약에서 쓸 수 있습니다")
        self._show_preview(names)

    def _analyze(self) -> None:
        from etreport.data import metrology as mt
        from etreport.ui.widgets.worker import run_in_background
        st = self.state
        if st.data is None or not st.met_columns:
            self.lbl.setText("먼저 [불러오기]로 조회한 뒤 [분석에 활용]을 누르세요")
            return
        et_items = [c for c in st.aliases() if c in st.data.columns] or [
            c for c in st.data.columns if c not in st.met_columns]
        # 계측 인자 × ET item 전수 훑기 — item이 많으면 수십 초가 걸린다
        self.lbl.setText("유의 인자 분석 중…")
        run_in_background(
            self, "유의 인자 분석",
            lambda: mt.top_factors(st.data, st.met_columns, et_items,
                                   excluded=st.hidden(), k=self.spin_k.value()),
            done=self._analyze_done)

    def _analyze_done(self, top) -> None:
        st = self.state
        st.met_top = top
        if top.is_empty():
            self.lbl.setText("유의 인자를 찾지 못했습니다 (점이 3개 미만일 수 있습니다)")
            self.table.setRowCount(0)
            return
        self.lbl.setText(f"top {top.height}개 — [PPT 생성] 때 슬라이드로 나갑니다")
        self._show_table(top)

    # ── 표시 ─────────────────────────────────────────────────
    def _met_wide(self):
        """조회 원본(`self.met`)을 현재 level 규칙으로 (lot, wafer) × 계측 열로."""
        from etreport.data import metrology as mt
        if self.met is None or self.met.is_empty():
            return None
        return mt.to_wide(self.met, self.level())

    def _show_met_preview(self) -> None:
        """조회 결과 그 자체를 미리보기 — 분석 데이터에 붙이기 전 모습."""
        wide = self._met_wide()
        if wide is None or wide.is_empty():
            self.table.setRowCount(0)
            return
        self._fill(wide.columns,
                   wide.sort(["lot", "wafer"]).head(50).iter_rows())

    def _show_preview(self, names: list[str]) -> None:
        st = self.state
        if not names or st.data is None:
            self.table.setRowCount(0)
            return
        cols = ["lot", "wafer", *names]
        sub = (st.data.select([c for c in cols if c in st.data.columns])
               .unique(subset=["lot", "wafer"]).sort(["lot", "wafer"]).head(50))
        self._fill(sub.columns, sub.iter_rows())

    def _show_table(self, top) -> None:
        self._fill(top.columns, top.iter_rows())

    def _fill(self, headers, rows) -> None:
        rows = list(rows)
        self.table.setColumnCount(len(headers))
        self.table.setRowCount(len(rows))
        self.table.setHorizontalHeaderLabels(list(headers))
        self.table.verticalHeader().setVisible(False)
        for r, rec in enumerate(rows):
            for c, val in enumerate(rec):
                text = ("" if val is None else
                        f"{val:.4g}" if isinstance(val, float) else str(val))
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
