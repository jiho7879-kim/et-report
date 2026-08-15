"""그룹 편집 — 단일 lot(4단 필터+조회+화살표) / 멀티 lot(붙여넣기) / 스타일 일괄.

확정 사양(§9.1) 세 가지를 지킨다.

  - **lot → step_id → total_site_cnt → temperature 순으로 좁힌다.** 앞을 바꾸면
    뒤 콤보 목록과 유효 wafer가 다시 채워진다
  - **배정도 필터 범위에만 적용한다.** 같은 wafer라도 step·온도·site가 다르면
    다른 측정점이다
  - **[조회]는 [적용] 없이도 동작한다.** DB 경로만 있으면 그 자리에서 읽기
    전용으로 열어 조회한다 — 대부분의 사용자가 "DB만 고르고 그룹부터 짜는"
    흐름을 쓴다

lot 찾기는 정확히 일치 → 대소문자 무시 → 부분 일치로 넓혀가고, 그래도 없으면
비슷한 lot을 예시로 보여준다.
"""
from __future__ import annotations

import polars as pl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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

from etreport.data import loader
from etreport.model import wafers as wnorm
from etreport.model.split import PALETTE_OKABE, REF_COLOR, SYMBOLS
from etreport.model.state import AppState

ALL = "전체"                       # 필터 콤보의 '좁히지 않음'
FILTERS = (("step_id", "step"), ("site", "site"), ("temp", "temp"))

#: 머리글로 인정하는 단어 — 있으면 첫 줄을 건너뛰고, 없으면 첫 줄도 자료로 본다
_HEAD_WORDS = {"lot", "lot_id", "root_lot_id", "랏", "로트",
               "wafer", "wafer_id", "slot", "slot_no", "웨이퍼",
               "group", "grp", "그룹", "조건"}


def parse_group_rows(text: str) -> list[tuple[str, str, str]]:
    """붙여넣은 표 → [(lot, wafer, group)] — **머리글은 있어도 없어도 같다**.

    첫 줄에 lot/wafer/group 같은 낱말이 있으면 머리글로 보고 건너뛴다.
    머리글이 있으면 열 이름으로 위치를 잡고(순서가 달라도 된다), 없으면
    `lot wafer group`(3열) 또는 `lot group`(2열) 순으로 읽는다.
    wafer는 비워 둘 수 있다 — 그러면 그 lot 전체가 대상이다.
    """
    rows = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not rows:
        return []

    def cells(line: str) -> list[str]:
        sep = "\t" if "\t" in line else ","
        return [c.strip() for c in line.split(sep)]

    first = cells(rows[0])
    is_head = any(c.strip().lower() in _HEAD_WORDS for c in first)
    order = ["lot", "wafer", "group"]
    if is_head:
        order = []
        for c in first:
            low = c.strip().lower()
            if low in ("lot", "lot_id", "root_lot_id", "랏", "로트"):
                order.append("lot")
            elif low in ("wafer", "wafer_id", "slot", "slot_no", "웨이퍼"):
                order.append("wafer")
            elif low in ("group", "grp", "그룹", "조건"):
                order.append("group")
            else:
                order.append("")
        rows = rows[1:]
    out: list[tuple[str, str, str]] = []
    for line in rows:
        parts = cells(line)
        if len(parts) < 2:
            continue
        if is_head:
            got = {name: parts[i] for i, name in enumerate(order)
                   if name and i < len(parts)}
            lot, wf, grp = (got.get("lot", ""), got.get("wafer", ""),
                            got.get("group", ""))
        elif len(parts) >= 3:
            lot, wf, grp = parts[0], parts[1], parts[2]
        else:                                # lot + group (wafer 열 없음)
            lot, wf, grp = parts[0], "", parts[1]
        if lot and grp:
            out.append((lot, wf, grp))
    return out


_PALETTES = {
    "Okabe-Ito (색약 안전)": PALETTE_OKABE,
    "System": ["#ff9500", "#0071e3", "#34c759", "#af52de", "#ff3b30", "#5ac8fa"],
    "그레이스케일": ["#1d1d1f", "#5a5a5f", "#8e8e93", "#aeaeb2", "#c7c7cc"],
}


class GroupDialog(QDialog):
    def __init__(self, state: AppState, parent=None, db_path: str = "") -> None:
        super().__init__(parent)
        self.state = state
        # [적용]으로 이미 연 DB가 있으면 그것, 없으면 도크에 골라만 둔 경로
        self.db_path = state.db_path or db_path
        self.index = loader.wafer_index_empty()
        self._lot: str | None = None
        self.setWindowTitle("그룹 편집")
        self.resize(820, 620)
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

        dbrow = QHBoxLayout()
        dbrow.addWidget(QLabel("DB"))
        self.lbl_db = QLabel()
        self.lbl_db.setObjectName("hint")
        dbrow.addWidget(self.lbl_db, 1)
        b = QPushButton("DB 선택")
        b.setProperty("ghost", True)
        b.clicked.connect(self._pick_db)
        dbrow.addWidget(b)
        v.addLayout(dbrow)

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

        # 4단 연쇄 필터 — lot 다음은 step_id → site → temperature 순(확정)
        fl = QHBoxLayout()
        self.filters: dict[str, QComboBox] = {}
        for label, name in FILTERS:
            fl.addWidget(QLabel(label))
            cmb = QComboBox()
            cmb.setMinimumWidth(110)
            cmb.currentIndexChanged.connect(
                lambda _i, n=name: self._filter_changed(n))
            self.filters[name] = cmb
            fl.addWidget(cmb)
        fl.addStretch(1)
        # 자동 그룹핑 — 모드는 사용자가 고른다(wafer별 / lot별)
        fl.addWidget(QLabel("자동 그룹핑"))
        self.cmb_auto = QComboBox()
        self.cmb_auto.addItems(["wafer별", "lot별"])
        fl.addWidget(self.cmb_auto)
        b = QPushButton("실행")
        b.setToolTip("조회 범위를 wafer(또는 lot)마다 그룹으로 자동 배정합니다.\n"
                     "다시 실행하면 이전 자동 배정은 지우고 새로 만듭니다.")
        b.clicked.connect(self._auto_clicked)
        fl.addWidget(b)
        self.lbl_auto = QLabel()
        self.lbl_auto.setObjectName("hint")
        fl.addWidget(self.lbl_auto)
        v.addLayout(fl)

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
            btn.setProperty("ghost", True)
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
        self._load_index()           # [적용] 전이면 DB에서 직접 읽는다
        if not self.index.is_empty():
            self.ed_lot.setText(sorted(set(self.index["lot"]))[0])
            self._lookup()
        return w

    # ── 자동 그룹핑 (wafer별 / lot별) ────────────────────────
    def auto_group(self, mode: str | None = None) -> int:
        """조회 범위를 wafer별 또는 lot별로 자동 배정. 만든 그룹 수를 반환.

        재실행해도 결과가 같도록(멱등) 기존 손배정을 먼저 지운다. 그룹 이름은
        wafer ID(또는 lot ID)를 그대로 쓰고, 첫 그룹은 기존 `_add_group` 규칙대로
        REF가 된다. [적용] 전에도 색인(wafer_index)만으로 동작한다.
        """
        from etreport.model.specs import GroupStyle

        st = self.state
        mode = mode or self.auto_mode()
        if self.index.is_empty():
            return 0
        keys = (["lot"] if mode == "lot" else ["lot", "wafer"])
        # DB 조회 순서는 보장되지 않는다 — lot·wafer 순으로 정렬해 그룹 번호와
        # 색이 실행할 때마다 달라지지 않게 한다
        rows = (self.index.select(keys).unique().sort(keys)
                .iter_rows(named=True))
        st.manual_groups.clear()               # 멱등 — 다시 돌리면 처음부터
        st.groups = []
        made = 0
        for i, rec in enumerate(rows):
            lot = rec["lot"]
            name = lot if mode == "lot" else rec["wafer"]
            gid = f"a{i}"
            st.groups.append(GroupStyle(
                gid=gid, name=name,
                color=REF_COLOR if i == 0 else PALETTE_OKABE[i % len(PALETTE_OKABE)],
                symbol="d" if i == 0 else SYMBOLS[i % len(SYMBOLS)],
                ref=i == 0))
            members = (self.index.filter(pl.col("lot") == lot) if mode == "lot"
                       else self.index.filter((pl.col("lot") == lot)
                                              & (pl.col("wafer") == rec["wafer"])))
            for w in dict.fromkeys(members["wafer"].to_list()):
                st.manual_groups[(lot, w, None, None, None)] = gid
            made += 1
        if st.data is not None:
            st.data = loader.apply_manual_groups(st.data, st.manual_groups)
        self._fill_groups()
        return made

    def auto_mode(self) -> str:
        return "lot" if self.cmb_auto.currentIndex() == 1 else "wafer"

    def _auto_clicked(self) -> None:
        n = self.auto_group()
        self.lbl_auto.setText(f"{n}개 그룹 생성" if n else "조회 결과가 없습니다")
        self._refresh_lists()

    # ── 데이터 원천 ──────────────────────────────────────────
    def _load_index(self) -> None:
        """(lot, wafer, step, temp, site, 포인트 수) 색인을 만든다.

        [적용]으로 읽어 둔 프레임이 있으면 그것을, 없으면 DB를 읽기 전용으로
        열어 조회한다 — [적용] 없이도 조회가 되어야 한다(§9.1).
        """
        st = self.state
        self.lbl_db.setText(self.db_path or "(DB를 고르세요)")
        try:
            if st.data is not None and st.data.height:
                self.index = loader.wafer_index_from_frame(st.data)
                self.lbl_db.setText(f"{self.db_path or st.db_label} · 불러옴")
            elif self.db_path:
                self.index = loader.wafer_index_from_db(self.db_path)
                self.lbl_db.setText(f"{self.db_path} · 읽기 전용으로 조회")
            else:
                self.index = loader.wafer_index_empty()
        except Exception as e:                       # noqa: BLE001 — 창은 살린다
            self.index = loader.wafer_index_empty()
            self.lbl_db.setText(f"DB를 읽지 못했습니다: {e}")

    def _pick_db(self) -> None:
        """DB를 바꾸면 **DB에서 파생된 것만** 새 DB 기준으로 다시 만든다.

        이전 DB의 (lot, wafer) 배정은 새 DB와 무관하므로 비우고, 자동 그룹핑을
        현재 모드로 다시 돌린다. 그룹 정의(이름·색·심볼·REF)는 자동 그룹핑이
        다시 만들어 준다 — 손으로 고른 스타일까지 지키려면 같은 DB를 유지한다.
        """
        p, _ = QFileDialog.getOpenFileName(self, "DuckDB 파일", "",
                                           "DuckDB (*.duckdb)")
        if not p:
            return
        changed = p != self.db_path
        self.db_path = p
        self.state.data = None if self.state.db_path != p else self.state.data
        self._load_index()
        if changed:
            self.state.manual_groups.clear()   # 이전 DB의 배정은 무의미하다
            n = self.auto_group()
            self.lbl_auto.setText(f"새 DB 기준 {n}개 그룹" if n else "")
        self._lookup()

    # ── 필터 ─────────────────────────────────────────────────
    def _selected(self, upto: str | None = None) -> dict[str, str]:
        """현재 필터 선택값. upto를 주면 **그 앞 단계까지만** 돌려준다.

        연쇄 필터의 핵심 — 뒤 콤보의 목록은 앞 단계로 좁힌 결과에서 뽑는다.
        """
        out: dict[str, str] = {}
        for _label, name in FILTERS:
            if name == upto:
                break
            cmb = self.filters.get(name)
            if cmb is not None and cmb.currentText() not in ("", ALL):
                out[name] = cmb.currentText()
        return out

    def _rows(self, conds: dict[str, str] | None = None) -> pl.DataFrame:
        """lot + 조건으로 좁힌 색인."""
        if self._lot is None or self.index.is_empty():
            return loader.wafer_index_empty()
        sub = self.index.filter(pl.col("lot") == self._lot)
        for name, val in (self._selected() if conds is None else conds).items():
            sub = sub.filter(pl.col(name) == val)
        return sub

    def _refresh_filters(self) -> None:
        """앞 단계로 좁힌 결과에서 각 콤보의 목록을 다시 만든다."""
        for _label, name in FILTERS:
            cmb = self.filters[name]
            keep = cmb.currentText()
            vals = sorted({v for v in self._rows(self._selected(upto=name))[name]
                           if v is not None})
            cmb.blockSignals(True)               # 갱신 중 재귀 방지
            cmb.clear()
            cmb.addItems([ALL, *vals])
            cmb.setCurrentIndex(cmb.findText(keep) if keep in vals else 0)
            cmb.setEnabled(bool(vals))
            cmb.blockSignals(False)

    def _filter_changed(self, name: str) -> None:
        self._refresh_filters()
        self._update_counts()
        self._refresh_lists()

    def _update_counts(self) -> None:
        sub = self._rows()
        if self._lot is None:
            return
        wafers = sorted(set(sub["wafer"]))
        pts = int(sub["n"].sum() or 0)
        self.lbl_valid.setText(f"유효 {len(wafers)}장 · {pts:,}포인트")

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
        for key in [k for k, v in st.manual_groups.items() if v == g.gid]:
            del st.manual_groups[key]            # 배정 기록도 함께 지운다
        st.groups.remove(g)
        self._fill_groups()

    def _find_lot(self, typed: str) -> str | None:
        """정확히 일치 → 대소문자 무시 → 부분 일치 순으로 넓혀 찾는다."""
        lots = list(dict.fromkeys(self.index["lot"].to_list()))
        if typed in lots:
            return typed
        low = typed.casefold()
        for lot in lots:
            if lot.casefold() == low:
                return lot
        hits = [lot for lot in lots if low in lot.casefold()]
        return hits[0] if len(hits) == 1 else None

    def _similar(self, typed: str, n: int = 5) -> list[str]:
        """못 찾았을 때 보여줄 비슷한 lot — 앞글자가 겹치는 것 우선."""
        lots = list(dict.fromkeys(self.index["lot"].to_list()))
        low = typed.casefold()
        scored = sorted(lots, key=lambda x: (
            -len([1 for a, b in zip(x.casefold(), low) if a == b]), x))
        return scored[:n]

    def _lookup(self) -> None:
        typed = self.ed_lot.text().strip()
        if not typed:
            return
        if self.index.is_empty():
            self._load_index()                   # DB만 고르고 바로 조회하는 흐름
        if self.index.is_empty():
            self.lbl_valid.setText(
                "DB를 읽지 못했습니다 — [DB 선택]으로 파일을 고르세요")
            self._clear_lists()
            return
        lot = self._find_lot(typed)
        if lot is None:
            hint = ", ".join(self._similar(typed))
            self.lbl_valid.setText(f"'{typed}' 없음 — 비슷한 lot: {hint}"
                                   if hint else f"'{typed}' 없음")
            self._lot = None
            self._clear_lists()
            return
        self._lot = lot
        if lot != typed:
            self.ed_lot.setText(lot)             # 찾은 이름으로 맞춰 준다
        self._refresh_filters()
        self._update_counts()
        self._refresh_lists()

    def _clear_lists(self) -> None:
        self.list_pool.clear()
        self.list_grp.clear()

    # ── 배정 ─────────────────────────────────────────────────
    def _key(self, wafer: str) -> tuple:
        """배정 키 — 고른 필터까지 포함한다(§9.1 '배정도 필터 범위에만')."""
        sel = self._selected()
        return (self._lot, wafer, sel.get("step"), sel.get("temp"),
                sel.get("site"))

    def _gid_of(self, wafer: str) -> str:
        """현재 필터 범위에서 이 wafer가 어느 그룹인지."""
        st = self.state
        if st.data is not None and "gid" in st.data.columns:
            sub = st.data.filter((pl.col("lot") == self._lot)
                                 & (pl.col("wafer") == wafer))
            for name, val in self._selected().items():
                if name in sub.columns:
                    sub = sub.filter(pl.col(name).cast(pl.Utf8) == val)
            if not sub.is_empty():
                return sub["gid"][0]
        return st.manual_groups.get(self._key(wafer), "")

    def _refresh_lists(self) -> None:
        if not hasattr(self, "list_grp"):        # 초기화 중 호출 방어
            return
        self._clear_lists()
        if self._lot is None:
            return
        group = self._current_group()            # 그룹이 없을 수 있다
        gid = group.gid if group else ""
        pool, mine = [], []
        for wf in sorted(set(self._rows()["wafer"])):
            g = self._gid_of(wf)
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
        """배정은 **고른 필터 범위에만** 걸린다. [적용] 전에도 기록해 둔다."""
        st = self.state
        if self._lot is None:
            return
        for wf in wafers:
            st.manual_groups[self._key(wf)] = gid
        if st.data is not None:
            st.data = loader.apply_manual_groups(st.data, st.manual_groups)
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

    def _lot_wafers(self, lot: str) -> list[str]:
        """그 lot에 실제로 있는 wafer 표기 목록 (색인 → 없으면 프레임)."""
        st = self.state
        if not self.index.is_empty():
            hit = self.index.filter(
                wnorm.lot_key_expr("lot") == wnorm.norm_lot(lot))["wafer"].to_list()
            if hit:
                return sorted(dict.fromkeys(hit))
        if st.data is not None and "lot" in st.data.columns:
            hit = st.data.filter(
                wnorm.lot_key_expr("lot") == wnorm.norm_lot(lot))["wafer"].to_list()
            return sorted(dict.fromkeys(hit))
        return []

    def _apply_paste(self) -> None:
        """붙여넣은 lot·wafer·group 표를 반영. [적용] 전에도 기록해 둔다.

        표기는 관대하게 받는다 — **머리글은 있어도 없어도 되고**, wafer는
        `W01` `W1` `01` `1` 이 모두 같은 wafer로 붙는다(§13). 실제 DB에 있는
        표기를 찾아 그 값으로 기록하므로 나중에 [적용]해도 배정이 유지된다.
        """
        st = self.state
        text = self.paste.toPlainText().strip()
        if not text:
            return
        name_to_gid = {g.name.strip().casefold(): g.gid for g in st.groups}
        if not name_to_gid:
            QMessageBox.information(self, "그룹",
                                    "먼저 ＋로 그룹을 만들거나 실험 조건을 적용하세요")
            return
        rows = parse_group_rows(text)
        n, miss_grp, miss_wf = 0, set(), []
        for lot, waf, grp in rows:
            gid = name_to_gid.get(grp.strip().casefold())
            if gid is None:
                miss_grp.add(grp)
                continue
            known = self._lot_wafers(lot)
            lot_actual = self._find_lot(lot) or lot
            if waf:
                actual = wnorm.resolve(waf, known)
                if actual is None:
                    # DB에 없는 표기라도 기록은 남긴다 — 나중에 [적용]으로 그
                    # lot이 들어오면 정규화 비교로 붙는다
                    miss_wf.append(f"{lot}·{waf}")
                    actual = waf
                targets = [actual]
            else:                       # wafer 열이 없으면 lot 전체
                targets = known
            for w in targets:           # 붙여넣기는 조건을 안 따진다(전체 범위)
                st.manual_groups[(lot_actual, w, None, None, None)] = gid
            n += 1
        if st.data is not None:
            st.data = loader.apply_manual_groups(st.data, st.manual_groups)
        msg = [f"{n}행을 반영했습니다"]
        if miss_grp:
            msg.append("없는 그룹 이름: " + ", ".join(sorted(miss_grp)[:5]))
        if miss_wf:
            msg.append(f"DB에서 못 찾은 wafer {len(miss_wf)}건: "
                       + ", ".join(miss_wf[:5]))
        QMessageBox.information(self, "적용됨", "\n".join(msg))
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
        b.setProperty("ghost", True)
        b.clicked.connect(self._apply_bulk)
        h.addWidget(b)
        h.addSpacing(12)
        self.cmb_pal = QComboBox()
        self.cmb_pal.addItems(list(_PALETTES))
        h.addWidget(self.cmb_pal)
        b2 = QPushButton("색 다시 배정")
        b2.setProperty("ghost", True)
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
