"""실험 조건 — 불러오기 창(파일/붙여넣기)과 factor 선택 창.

`SplitSourceDialog`: **입력 경로 3가지를 한 창에서**(§3.4 확정) — 엑셀 파일 /
CSV·TSV / 클립보드 붙여넣기. 붙여넣으면 **즉시 파싱해** 미리보기와 step 목록을
보여 준다(엑셀이 편하지만 Excel 없는 PC도 있고, 붙여넣기가 더 빠를 때가 많다).

`SplitDialog`: 매트릭스 확인 · factor 선택 · 혼입 경고 · 그룹 미리보기.
"""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from etreport.model.state import AppState


def matrix_to_tsv(sm) -> str:
    """SplitMatrix → 붙여넣기 칸에 넣을 TSV 표(첫 줄 머리글).

    fab tracking 자동 조회 결과를 **붙여넣기와 같은 형식**으로 되돌려 놓기
    위한 것 — 저장·재적용 경로를 하나로 유지한다.
    """
    if sm is None or sm.wide is None:
        return ""
    cols = ["lot", "wafer", *sm.steps]
    lines = ["\t".join(cols)]
    for rec in sm.wide.iter_rows(named=True):
        lines.append("\t".join("" if rec.get(c) is None else str(rec[c])
                               for c in cols))
    return "\n".join(lines)


class SplitSourceDialog(QDialog):
    """실험 조건 불러오기 — 파일(엑셀·CSV·TSV) 또는 붙여넣기.

    결과는 `matrix`(SplitMatrix)와 출처 두 가지로 남는다. 파일이면 `path`,
    붙여넣기면 `text` — 설정에 저장돼 [적용] 때 같은 방식으로 다시 읽힌다.
    """

    def __init__(self, parent=None, path: str = "", text: str = "",
                 baseline: str = "", baseline_lot: str = "",
                 lots: list[str] | None = None, state=None) -> None:
        super().__init__(parent)
        from etreport.model.split import BASELINE_DEFAULT
        # fab tracking 조회 창에 넘길 상태(조건 자동 채움·컬럼 붙이기). 없어도
        # 창은 열린다 — 파일·붙여넣기 경로는 상태가 필요 없다.
        self.state = state
        self.setWindowTitle("실험 조건 불러오기")
        self.resize(720, 620)
        self.matrix = None
        self.cleared = False
        self.path, self.text = path, text
        self.baseline = baseline or BASELINE_DEFAULT
        # 기준(REF)으로 삼을 lot — 정해 두면 step마다 그 lot의 다수 조건이
        # 기준이 된다(§9.2). 도크에서 고른 lot이 조회의 기본값이 된다.
        self.baseline_lot = baseline_lot
        self.lots = list(lots or [])
        self.tracking = None                   # fab tracking 원본(기능 A)

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        for label, patterns in (("엑셀 파일", "엑셀 (*.xlsx *.xlsm)"),
                                ("CSV · TSV", "표 파일 (*.csv *.tsv *.txt)")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, p=patterns: self._pick(p))
            top.addWidget(b)
        b = QPushButton("fab tracking 자동")
        b.setToolTip("사내 fab.f_fab_tracking에서 이 lot들의 split 조건을 읽어\n"
                     "조건이 갈리는 step만 실험 축으로 올립니다.\n"
                     "(PHOTO는 recipe, 그 외는 ppid 기준)")
        b.clicked.connect(self._from_tracking)
        top.addWidget(b)
        self.lbl_src = QLabel(path or "(파일을 고르거나 아래에 붙여넣으세요)")
        self.lbl_src.setObjectName("hint")
        top.addWidget(self.lbl_src, 1)
        v.addLayout(top)

        v.addWidget(QLabel("또는 엑셀에서 복사해 붙여넣기 "
                           "(첫 줄이 머리글 · lot·wafer + step 열)"))
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText(
            "lot\twafer\tM1\tM5\nPA123\t01\tBase\tBase\nPA123\t02\tHi\tBase")
        self.paste.setMaximumHeight(150)
        v.addWidget(self.paste)

        self.lbl_info = QLabel()
        self.lbl_info.setObjectName("hint")
        self.lbl_info.setWordWrap(True)
        v.addWidget(self.lbl_info)
        self.preview = QTableWidget()
        self.preview.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.preview, 1)

        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        # 뗄 자리는 **붙인 자리와 같은 창**에 둔다 — 파일을 고를 곳은 여기뿐인데
        # 해제만 다른 데 있으면 한 번 연결한 조건을 되돌릴 길이 보이지 않는다.
        self.btn_clear = self.bb.addButton("연결 해제",
                                           QDialogButtonBox.ResetRole)
        self.btn_clear.setToolTip("실험 조건을 떼고 factor 그룹을 되돌립니다")
        self.btn_clear.setEnabled(bool(path or text))
        self.btn_clear.clicked.connect(self._clear)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        v.addWidget(self.bb)

        # **연결은 위젯을 다 만든 뒤에.** 예전에는 textChanged를 먼저 잇고
        # setPlainText로 저장된 내용을 채워서, 아직 만들어지지 않은
        # self.preview를 _show()가 건드리며 창이 뜨자마자 죽었다
        # (AttributeError: 'SplitSourceDialog' object has no attribute 'preview').
        self.paste.textChanged.connect(self._text_changed)   # 즉시 파싱
        if text:
            self.paste.setPlainText(text)
        elif path:
            self._load_file(path)
        self._sync_ok()

    # ── 입력 ─────────────────────────────────────────────────
    def _clear(self) -> None:
        """출처를 떼고 창을 닫는다 — `cleared`를 보고 부른 쪽이 설정을 비운다."""
        self.cleared = True
        self.matrix, self.path, self.text = None, "", ""
        self.baseline_lot = ""
        self.accept()

    def _from_tracking(self) -> None:
        """fab tracking 조회 창을 연다(기능 A).

        예전에는 여기서 lot만 물어보고 바로 조회했다. 그러면 (1) line·process·
        part·기간을 손으로 적을 자리가 없고 (2) 결과 컬럼 이름이 `process_id`
        값으로 고정됐다. 조건·SQL·컬럼 이름을 정하는 일은 전부
        `FabTrackDialog`가 맡고, 여기서는 **결과를 받아 붙여넣기 칸에 표(TSV)로**
        채운다 — 그래야 설정에 저장되고 [적용]에서 같은 조건이 그대로 다시
        읽힌다(예전에는 창을 닫는 순간 matrix가 사라졌다).
        """
        from etreport.ui.widgets.fabtrack_dialog import FabTrackDialog

        dlg = FabTrackDialog(self.state_for_tracking(), self, lots=self.lots)
        if not dlg.exec():
            return
        dlg.apply_to_state()               # 이름 붙인 컬럼을 분석 프레임에 붙인다
        if dlg.tracking is not None:
            self._tracking_done(dlg.tracking)

    def state_for_tracking(self):
        """조회 창에 넘길 AppState. 창을 띄운 쪽이 갖고 있으면 그걸 쓴다.

        [실험 조건] 창은 상태 없이도 열리는(파일만 읽는) 창이라 AppState를 들고
        있지 않을 수 있다. 그때는 빈 상태를 만들어 넘긴다 — 조건 자동 채움만
        비고 나머지는 그대로 동작한다.
        """
        from etreport.model.state import AppState
        return self.state if getattr(self, "state", None) is not None else AppState()

    def _tracking_done(self, df) -> None:
        """조회 결과로 매트릭스를 만들고, lot이 여럿이면 기준 lot을 물어본다.

        lot마다 POR이 다를 수 있어 "어느 lot을 기준으로 볼지"는 사람이 정해야
        하는 판단이다(§9.2). lot이 하나면 물을 것이 없으므로 건너뛴다.
        """
        from etreport.data import fabtracking as ft
        self.tracking = df
        sm = ft.to_split_matrix(df)
        if sm.wide is None or not sm.steps:
            self.lbl_src.setText(
                ft.summarize(df) + " — 조건이 갈리는 step이 없습니다")
            return
        lots = sorted(set(sm.wide["lot"].to_list()))
        if len(lots) > 1:
            from PySide6.QtWidgets import QInputDialog
            AUTO = "(자동 — 전체에서 가장 흔한 조건)"
            pick, ok = QInputDialog.getItem(
                self, "기준(REF) lot",
                "어느 lot을 기준으로 볼까요?\n"
                "고르면 step마다 그 lot의 다수 조건이 기준이 됩니다.",
                [AUTO, *lots], 0, False)
            if ok and pick != AUTO:
                self.baseline_lot = pick
                sm = ft.to_split_matrix(df, baseline_lot=pick)
        self.baseline = sm.baseline
        self.path = ""
        self.lbl_src.setText(
            ft.summarize(df) + f" · 기준(REF) {sm.baseline_label()}")
        # 표로 채워 넣으면 나머지는 붙여넣기 경로와 완전히 같아진다
        self.paste.setPlainText(matrix_to_tsv(sm))

    def _pick(self, patterns: str) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "실험 조건 파일", "", patterns)
        if p:
            self._load_file(p)

    def _load_file(self, path: str) -> None:
        from etreport.model.split import BASELINE_DEFAULT, load_split_file
        self.baseline = BASELINE_DEFAULT      # 파일은 'Base'가 기준이다
        try:
            self.matrix = load_split_file(path, self.baseline)
            self.path, self.text = path, ""
            self.paste.blockSignals(True)      # 파일을 고르면 붙여넣기는 비운다
            self.paste.clear()
            self.paste.blockSignals(False)
            self.lbl_src.setText(path)
        except Exception as e:                 # noqa: BLE001 — 창은 살린다
            self.matrix = None
            self.lbl_src.setText(f"읽지 못했습니다: {e}")
        self._show()

    def _text_changed(self) -> None:
        from etreport.model.split import parse_split_text
        raw = self.paste.toPlainText().strip()
        if not raw:
            self.matrix, self.text = None, ""
            self._show()
            return
        try:
            self.matrix = parse_split_text(raw, self.baseline,
                                           self.baseline_lot)
            self.path, self.text = "", raw
            self.lbl_src.setText("붙여넣은 내용")
        except Exception as e:                 # noqa: BLE001 — 타이핑 중일 뿐이다
            self.matrix = None
            self.lbl_info.setText(f"아직 읽을 수 없습니다: {e}")
        self._show()

    # ── 미리보기 ─────────────────────────────────────────────
    def _show(self) -> None:
        sm = self.matrix
        self.preview.clear()
        if sm is None or sm.wide is None:
            self.preview.setRowCount(0)
            self.preview.setColumnCount(0)
            self._sync_ok()
            return
        df = sm.wide
        cols = ["lot", "wafer", *sm.steps]
        self.preview.setColumnCount(len(cols))
        self.preview.setRowCount(min(df.height, 50))
        self.preview.setHorizontalHeaderLabels(cols)
        self.preview.verticalHeader().setVisible(False)
        for r, rec in enumerate(df.head(50).iter_rows(named=True)):
            for c, col in enumerate(cols):
                self.preview.setItem(r, c, QTableWidgetItem(str(rec[col])))
        self.preview.resizeColumnsToContents()
        self.lbl_info.setText(
            f"lot {df['lot'].n_unique()} · wafer {df.height}행 · "
            f"step {len(sm.steps)}개: {', '.join(sm.steps)} · "
            f"기준(REF) {sm.baseline_label()}"
            + ("  (미리보기 50행)" if df.height > 50 else ""))
        self._sync_ok()

    def _sync_ok(self) -> None:
        self.bb.button(QDialogButtonBox.Ok).setEnabled(self.matrix is not None)


class SplitDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("실험 조건")
        self.resize(760, 620)
        v = QVBoxLayout(self)

        v.addWidget(QLabel("lot·wafer별 step 조건 코드"))
        self.table = QTableWidget()
        v.addWidget(self.table, 1)

        v.addWidget(QLabel("실험 축 (factor) — 비교하려는 step을 고르세요"))
        self.chk_host = QWidget()
        self.chk_lay = QHBoxLayout(self.chk_host)
        self.chk_lay.setContentsMargins(0, 0, 0, 0)
        self.checks: dict[str, QCheckBox] = {}
        for step in state.split.steps:
            cb = QCheckBox(step)
            cb.setChecked(step in state.factors)
            cb.toggled.connect(self._factors_changed)
            self.checks[step] = cb
            self.chk_lay.addWidget(cb)
        self.chk_lay.addStretch(1)
        v.addWidget(self.chk_host)

        self.lbl_warn = QLabel()
        self.lbl_warn.setObjectName("warnBox")
        self.lbl_warn.setWordWrap(True)
        v.addWidget(self.lbl_warn)

        v.addWidget(QLabel("자동 생성될 그룹"))
        self.lbl_groups = QLabel()
        self.lbl_groups.setWordWrap(True)
        v.addWidget(self.lbl_groups)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._fill_table()
        self._refresh()

    def _fill_table(self) -> None:
        sm = self.state.split
        df = sm.wide
        cols = ["lot", "wafer", *sm.steps]
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(df.height)
        self.table.setHorizontalHeaderLabels(cols)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, rec in enumerate(df.iter_rows(named=True)):
            for c, col in enumerate(cols):
                it = QTableWidgetItem(str(rec[col]))
                if col in self.state.factors:
                    it.setBackground(QColor("#eaf3fd"))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()

    def _factors_changed(self) -> None:
        picked = [s for s, cb in self.checks.items() if cb.isChecked()]
        if not picked:                       # 최소 하나는 유지
            first = next(iter(self.checks.values()))
            first.blockSignals(True)
            first.setChecked(True)
            first.blockSignals(False)
            picked = [next(iter(self.checks))]
        self.state.factors = picked
        self._fill_table()
        self._refresh()

    def _refresh(self) -> None:
        sm, f = self.state.split, self.state.factors
        cf = sm.confounds(f)
        if cf:
            lines = ["<b>⚠ 혼입 주의</b>"]
            for c in cf[:4]:
                lines.append(
                    f"‘{c.group}’ 그룹 안에 <b>{c.step}</b>가 "
                    f"{len(c.codes)}종 섞여 있습니다 ({', '.join(c.codes)})")
            lines.append(
                f"<span style='opacity:.85'>이 상태로는 차이가 "
                f"{'·'.join(f)} 때문인지 {cf[0].step} 때문인지 구분할 수 없습니다. "
                f"{cf[0].step}도 factor로 추가하거나 "
                f"{cf[0].step}={sm.baseline}인 wafer만 남기세요.</span>")
            self.lbl_warn.setText("<br>".join(lines))
            self.lbl_warn.setProperty("level", "warn")
        else:
            self.lbl_warn.setText(
                "선택한 factor 외 조건이 모든 그룹에서 균일합니다 · 비교 가능")
            self.lbl_warn.setProperty("level", "ok")
        self.lbl_warn.style().unpolish(self.lbl_warn)
        self.lbl_warn.style().polish(self.lbl_warn)

        parts = []
        for st in sm.styles_for(f):
            parts.append(
                f"<span style='color:{st.color}'>■</span> {st.name}"
                f"{' · REF' if st.ref else ''}")
        self.lbl_groups.setText("&nbsp;&nbsp;&nbsp;".join(parts))
