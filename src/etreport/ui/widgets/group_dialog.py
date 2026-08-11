"""그룹 편집 — 단일 lot(입력+조회+화살표) / 멀티 lot(붙여넣기) / 스타일 일괄."""
from __future__ import annotations

import polars as pl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from etreport.model.split import PALETTE_OKABE, REF_COLOR, SYMBOLS
from etreport.model.state import AppState

_PALETTES = {
    "Okabe-Ito (색약 안전)": PALETTE_OKABE,
    "System": ["#ff9500", "#0071e3", "#34c759", "#af52de", "#ff3b30", "#5ac8fa"],
    "그레이스케일": ["#1d1d1f", "#5a5a5f", "#8e8e93", "#aeaeb2", "#c7c7cc"],
}


class GroupDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("그룹 편집")
        self.resize(780, 560)
        v = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._single_tab(), "단일 lot")
        tabs.addTab(self._multi_tab(), "멀티 lot")
        v.addWidget(tabs, 1)
        v.addWidget(self._bulk_box())

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    # ── 단일 lot ─────────────────────────────────────────────
    def _single_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        top = QHBoxLayout()
        top.addWidget(QLabel("lot"))
        self.ed_lot = QLineEdit()
        self.ed_lot.setPlaceholderText("lot ID 입력 후 Enter 또는 조회")
        self.ed_lot.returnPressed.connect(self._lookup)
        top.addWidget(self.ed_lot, 1)
        b = QPushButton("조회")
        b.clicked.connect(self._lookup)
        top.addWidget(b)
        self.lbl_valid = QLabel()
        self.lbl_valid.setObjectName("hint")
        top.addWidget(self.lbl_valid)
        v.addLayout(top)

        mid = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("미배정 wafer"))
        self.list_pool = QListWidget()
        self.list_pool.setSelectionMode(QListWidget.ExtendedSelection)
        left.addWidget(self.list_pool)
        mid.addLayout(left, 1)

        arrows = QVBoxLayout()
        arrows.addStretch(1)
        for text, fn in (("→", lambda: self._move(True)),
                         ("←", lambda: self._move(False)),
                         ("≫", lambda: self._move_all(True)),
                         ("≪", lambda: self._move_all(False))):
            btn = QPushButton(text)
            btn.setFixedWidth(46)
            btn.clicked.connect(fn)
            arrows.addWidget(btn)
        arrows.addStretch(1)
        mid.addLayout(arrows)

        right = QVBoxLayout()
        gp = QHBoxLayout()
        self.cmb_group = QComboBox()
        self.cmb_group.currentIndexChanged.connect(self._refresh_lists)
        gp.addWidget(self.cmb_group, 1)
        for text, tip, fn in (("＋", "그룹 추가", self._add_group),
                              ("이름", "이름 변경", self._rename_group),
                              ("－", "그룹 삭제", self._del_group)):
            b = QPushButton(text)
            b.setProperty("ghost", True)
            b.setToolTip(tip)
            b.setMinimumWidth(44)
            b.clicked.connect(fn)
            gp.addWidget(b)
        right.addLayout(gp)
        self.list_grp = QListWidget()
        self.list_grp.setSelectionMode(QListWidget.ExtendedSelection)
        right.addWidget(self.list_grp)
        mid.addLayout(right, 1)
        v.addLayout(mid, 1)

        self._fill_groups()          # 리스트 위젯이 생긴 뒤에 채운다
        if self.state.data is not None and self.state.data.height:
            self.ed_lot.setText(sorted(set(self.state.data["lot"]))[0])
            self._lookup()
        return w

    # ── 그룹 목록 ────────────────────────────────────────────
    def _fill_groups(self) -> None:
        self.cmb_group.blockSignals(True)
        cur = self.cmb_group.currentIndex()
        self.cmb_group.clear()
        self.cmb_group.addItems([g.name for g in self.state.groups])
        if self.state.groups:
            self.cmb_group.setCurrentIndex(
                min(max(cur, 0), len(self.state.groups) - 1))
        self.cmb_group.blockSignals(False)
        self._refresh_lists()

    def _current_group(self):
        """선택된 그룹 — 없으면 None. 인덱스를 직접 쓰지 말 것."""
        i = self.cmb_group.currentIndex()
        if 0 <= i < len(self.state.groups):
            return self.state.groups[i]
        return None

    def _add_group(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        from etreport.model.specs import GroupStyle
        from etreport.model.split import PALETTE_OKABE, REF_COLOR, SYMBOLS
        name, ok = QInputDialog.getText(self, "그룹 추가", "이름")
        if not (ok and name.strip()):
            return
        st = self.state
        n = len(st.groups)
        is_ref = n == 0                       # 첫 그룹은 REF로
        st.groups.append(GroupStyle(
            gid=f"g{n}", name=name.strip(),
            color=REF_COLOR if is_ref else PALETTE_OKABE[n % len(PALETTE_OKABE)],
            symbol="d" if is_ref else SYMBOLS[n % len(SYMBOLS)],
            ref=is_ref))
        self._fill_groups()
        self.cmb_group.setCurrentIndex(len(st.groups) - 1)

    def _rename_group(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        g = self._current_group()
        if g is None:
            return
        name, ok = QInputDialog.getText(self, "이름 변경", "이름", text=g.name)
        if ok and name.strip():
            g.name = name.strip()
            self._fill_groups()

    def _del_group(self) -> None:
        g = self._current_group()
        if g is None:
            return
        st = self.state
        if st.data is not None:
            st.data = st.data.with_columns(
                pl.when(pl.col("gid") == g.gid).then(pl.lit(""))
                .otherwise(pl.col("gid")).alias("gid"))
        st.groups.remove(g)
        self._fill_groups()

    def _lookup(self) -> None:
        st = self.state
        lot = self.ed_lot.text().strip().upper()
        if st.data is None or not lot:
            return
        sub = st.data.filter(pl.col("lot") == lot)
        if sub.is_empty():
            self.lbl_valid.setText("조회 결과 없음 — 먼저 적재하세요")
            self.list_pool.clear()
            self.list_grp.clear()
            return
        self._lot = lot
        wafers = sorted(set(sub["wafer"]))
        self.lbl_valid.setText(f"유효 {len(wafers)}장")
        self._refresh_lists()

    def _refresh_lists(self) -> None:
        if not hasattr(self, "list_grp"):        # 초기화 중 호출 방어
            return
        st = self.state
        lot = getattr(self, "_lot", None)
        self.list_pool.clear()
        self.list_grp.clear()
        if lot is None or st.data is None:
            return
        group = self._current_group()           # 그룹이 없을 수 있다
        gid = group.gid if group else ""
        sub = st.data.filter(pl.col("lot") == lot)
        pool, mine = [], []
        for wf in sorted(set(sub["wafer"])):
            g = sub.filter(pl.col("wafer") == wf)["gid"][0]
            if group is not None and g == gid:
                mine.append(wf)
            elif not g:
                pool.append(wf)
        self.list_pool.addItems(pool)
        for wf in mine:
            it = QListWidgetItem(wf)
            if group is not None:
                it.setForeground(QColor(group.color))
            self.list_grp.addItem(it)

    def _assign(self, wafers: list[str], gid: str) -> None:
        st = self.state
        lot = getattr(self, "_lot", None)
        if st.data is None or lot is None:
            return
        st.data = st.data.with_columns(
            pl.when((pl.col("lot") == lot) & pl.col("wafer").is_in(wafers))
            .then(pl.lit(gid)).otherwise(pl.col("gid")).alias("gid"))
        self._refresh_lists()

    def _move(self, to_group: bool) -> None:
        g = self._current_group()
        if to_group and g is None:
            QMessageBox.information(self, "그룹", "먼저 ＋로 그룹을 만드세요")
            return
        src = self.list_pool if to_group else self.list_grp
        picked = [i.text() for i in src.selectedItems()]
        if picked:
            self._assign(picked, g.gid if to_group else "")

    def _move_all(self, to_group: bool) -> None:
        g = self._current_group()
        if to_group and g is None:
            QMessageBox.information(self, "그룹", "먼저 ＋로 그룹을 만드세요")
            return
        src = self.list_pool if to_group else self.list_grp
        allw = [src.item(i).text() for i in range(src.count())]
        if allw:
            self._assign(allw, g.gid if to_group else "")

    # ── 멀티 lot ─────────────────────────────────────────────
    def _multi_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "엑셀에서 lot · wafer · group 표를 복사해 붙여넣으세요.\n"
            "wafer 열이 없으면 해당 lot 전체가 그 그룹이 됩니다."))
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText(
            "lot\twafer\tgroup\nPA123\tW01\tSplit_A\nPA123\tW02\tSplit_A")
        v.addWidget(self.paste, 1)
        b = QPushButton("붙여넣은 내용 적용")
        b.clicked.connect(self._apply_paste)
        v.addWidget(b)
        return w

    def _apply_paste(self) -> None:
        st = self.state
        text = self.paste.toPlainText().strip()
        if not text or st.data is None:
            return
        name_to_gid = {g.name: g.gid for g in st.groups}
        if not name_to_gid:
            QMessageBox.information(self, "그룹",
                                    "먼저 ＋로 그룹을 만들거나 실험 조건을 적용하세요")
            return
        n = 0
        for line in text.splitlines()[1:]:
            parts = [p.strip() for p in line.replace(",", "\t").split("\t")]
            if len(parts) < 2:
                continue
            lot, wf, grp = [*parts, "", ""][:3] if len(parts) >= 3 \
                else (parts[0], "", parts[1])
            gid = name_to_gid.get(grp)
            if gid is None:
                continue
            cond = (pl.col("lot") == lot)
            if wf:
                wf2 = wf if wf.upper().startswith("W") else f"W{int(wf):02d}"
                cond = cond & (pl.col("wafer") == wf2)
            st.data = st.data.with_columns(
                pl.when(cond).then(pl.lit(gid)).otherwise(pl.col("gid")).alias("gid"))
            n += 1
        QMessageBox.information(self, "적용됨", f"{n}행을 반영했습니다")
        self._refresh_lists()

    # ── 스타일 일괄 ──────────────────────────────────────────
    def _bulk_box(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.addWidget(QLabel("일괄 적용"))
        self.cmb_sym = QComboBox()
        self.cmb_sym.addItems(["심볼 —", "● 원", "■ 사각", "▲ 삼각", "◆ 마름모", "＋ 십자"])
        h.addWidget(self.cmb_sym)
        self.cmb_size = QComboBox()
        self.cmb_size.addItems(["크기 —", "4", "6", "8", "10"])
        h.addWidget(self.cmb_size)
        b = QPushButton("모든 그룹에 적용")
        b.clicked.connect(self._apply_bulk)
        h.addWidget(b)
        h.addSpacing(12)
        self.cmb_pal = QComboBox()
        self.cmb_pal.addItems(list(_PALETTES))
        h.addWidget(self.cmb_pal)
        b2 = QPushButton("색 다시 배정")
        b2.clicked.connect(self._apply_palette)
        h.addWidget(b2)
        h.addStretch(1)
        return w

    def _apply_bulk(self) -> None:
        if not self.state.groups:
            QMessageBox.information(self, "그룹", "그룹이 없습니다")
            return
        si, zi = self.cmb_sym.currentIndex(), self.cmb_size.currentIndex()
        for g in self.state.groups:
            if si > 0:
                g.symbol = SYMBOLS[(si - 1) % len(SYMBOLS)]
            if zi > 0:
                g.size = int(self.cmb_size.currentText())
        QMessageBox.information(self, "적용됨",
                                f"{len(self.state.groups)}개 그룹에 반영했습니다")

    def _apply_palette(self) -> None:
        if not self.state.groups:
            return
        pal = _PALETTES[self.cmb_pal.currentText()]
        for i, g in enumerate(self.state.groups):
            g.color = REF_COLOR if (g.ref and pal is not _PALETTES["그레이스케일"]) \
                else pal[i % len(pal)]
        self._refresh_lists()
