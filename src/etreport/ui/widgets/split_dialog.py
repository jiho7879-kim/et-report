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


class SplitSourceDialog(QDialog):
    """실험 조건 불러오기 — 파일(엑셀·CSV·TSV) 또는 붙여넣기.

    결과는 `matrix`(SplitMatrix)와 출처 두 가지로 남는다. 파일이면 `path`,
    붙여넣기면 `text` — 설정에 저장돼 [적용] 때 같은 방식으로 다시 읽힌다.
    """

    def __init__(self, parent=None, path: str = "", text: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("실험 조건 불러오기")
        self.resize(720, 620)
        self.matrix = None
        self.path, self.text = path, text
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
        self.paste.textChanged.connect(self._text_changed)   # 즉시 파싱
        if text:
            self.paste.setPlainText(text)
        v.addWidget(self.paste)

        self.lbl_info = QLabel()
        self.lbl_info.setObjectName("hint")
        self.lbl_info.setWordWrap(True)
        v.addWidget(self.lbl_info)
        self.preview = QTableWidget()
        self.preview.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.preview, 1)

        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        v.addWidget(self.bb)
        if path:
            self._load_file(path)
        self._sync_ok()

    # ── 입력 ─────────────────────────────────────────────────
    def _from_tracking(self) -> None:
        """fab tracking에서 split 조건을 읽어 온다(기능 A).

        bdq가 없는 PC(사내 밖)에서는 조회가 안 되므로 안내만 남긴다 —
        이 코드베이스의 "중단하지 않고 알린다" 방식.
        """
        from PySide6.QtWidgets import QInputDialog

        from etreport.data import fabtracking as ft
        lots, ok = QInputDialog.getText(
            self, "fab tracking", "lot ID (쉼표로 여러 개, 비우면 전체)")
        if not ok:
            return
        wanted = [x.strip() for x in lots.replace(",", " ").split() if x.strip()]
        sql = ft.build_tracking_sql(lots=wanted or None)
        try:
            df = ft.fetch(sql)
        except ImportError:
            self.lbl_src.setText("bigdataquery가 없는 환경입니다 — 사내 PC에서 실행하세요")
            return
        except Exception as e:                 # noqa: BLE001 — 창은 살린다
            self.lbl_src.setText(f"fab tracking 조회 실패: {e}")
            return
        self.matrix = ft.to_split_matrix(df)
        self.path, self.text = "", ""
        self.tracking = df
        self.lbl_src.setText(ft.summarize(df))
        self._show()

    def _pick(self, patterns: str) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "실험 조건 파일", "", patterns)
        if p:
            self._load_file(p)

    def _load_file(self, path: str) -> None:
        from etreport.model.split import load_split_file
        try:
            self.matrix = load_split_file(path)
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
            self.matrix = parse_split_text(raw)
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
            f"step {len(sm.steps)}개: {', '.join(sm.steps)}"
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
