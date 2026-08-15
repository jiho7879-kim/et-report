"""inline 계측 창 — step_id·item_id 목록을 받아 분석 데이터에 붙인다(기능 B).

흐름은 셋뿐이다.
  1. 엑셀에서 복사한 `step_id  item_id` 목록을 붙여넣는다
  2. [불러오기] — **분석 중인 lot에 한해** `fab.f_fab_wf_met`를 조회해
     (lot, wafer)로 붙인다. site/wafer level은 확정 규칙을 따른다
  3. [유의 인자 분석] — 계측 인자 × ET item을 훑어 top-k를 뽑는다.
     결과는 상태에 남아 [PPT 생성] 때 슬라이드로 나간다

지연 계산 규약을 지킨다 — 이 창에서도 버튼을 눌러야 계산한다.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from etreport.model.state import AppState


class MetrologyDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.met = None                     # 조회 원본(polars)
        self.setWindowTitle("inline 계측 불러오기")
        self.resize(760, 640)

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "엑셀에서 복사한 step_id · item_id 목록을 붙여넣으세요 "
            "(비우면 CD|THK|DEPTH|TIP|RCS 전체)"))
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText("step_id\titem_id\nM1\tCD_A\nM5\tTHK_B")
        self.paste.setMaximumHeight(130)
        v.addWidget(self.paste)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("level"))
        self.cmb_level = QComboBox()
        self.cmb_level.addItems(["wafer (Q2)", "site (요약 제외)"])
        bar.addWidget(self.cmb_level)
        b = QPushButton("불러오기")
        b.clicked.connect(self._load)
        bar.addWidget(b)
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

    # ── 조회 ─────────────────────────────────────────────────
    def level(self) -> str:
        return "site" if self.cmb_level.currentIndex() == 1 else "wafer"

    def lots(self) -> list[str]:
        st = self.state
        if st.data is None or "lot" not in st.data.columns:
            return []
        return sorted(set(st.data["lot"]))

    def _load(self) -> None:
        """조회는 사내망 왕복이라 몇 초씩 걸린다 — 진행 창을 띄우고 워커에서."""
        from etreport.data import metrology as mt
        from etreport.ui.widgets.worker import bdq_call, run_in_background
        lots = self.lots()
        if not lots:
            self.lbl.setText("먼저 [적용]으로 분석 DB를 여세요 — lot을 알 수 없습니다")
            return
        pairs = mt.parse_pairs(self.paste.toPlainText())
        steps = sorted({s for s, _ in pairs}) or None
        items = sorted({i for _, i in pairs}) or None
        sql = mt.build_met_sql(lots, steps=steps, items=items,
                               item_regex=None if items else mt.ITEM_REGEX)
        self.lbl.setText("계측 조회 중…")
        run_in_background(self, "inline 계측 조회",
                          bdq_call(lambda: mt.fetch(sql)),
                          done=lambda df: self._load_done(df, len(lots)))

    def _load_done(self, met, n_lots: int) -> None:
        """조회 결과를 프레임에 붙인다 — UI 갱신은 여기서만."""
        from etreport.data import metrology as mt
        st = self.state
        self.met = met
        st.data, names = mt.attach(st.data, self.met, self.level())
        st.met_columns = sorted({*st.met_columns, *names})
        self.lbl.setText(
            f"lot {n_lots}개 · 계측 {len(names)}개 붙임 "
            f"({self.level()} level) — 탐색 X축과 Summary에서 쓸 수 있습니다")
        self._show_preview(names)

    def _analyze(self) -> None:
        from etreport.data import metrology as mt
        from etreport.ui.widgets.worker import run_in_background
        st = self.state
        if st.data is None or not st.met_columns:
            self.lbl.setText("먼저 [불러오기]로 계측값을 붙이세요")
            return
        et_items = [c for c in st.aliases() if c in st.data.columns] or [
            c for c in st.data.columns if c not in st.met_columns]
        # 계측 인자 × ET item 전수 훑기 — item이 많으면 수십 초가 걸린다
        self.lbl.setText("유의 인자 분석 중…")
        run_in_background(
            self, "유의 인자 분석",
            lambda: mt.top_factors(st.data, st.met_columns, et_items,
                                   excluded=st.excluded, k=self.spin_k.value()),
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
