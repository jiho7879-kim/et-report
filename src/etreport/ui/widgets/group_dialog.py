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
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
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

#: 자동 그룹핑 콤보의 라벨 → 모드. 멀티 lot 탭은 여기에 'split factor별'이 더 붙는다
#: (단일 lot 탭에서는 lot 경계를 넘는 묶음이 의미가 없다).
AUTO_MODES = {"wafer별": "wafer", "lot별": "lot", "split factor별": "factor"}

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

    # ── 자동 그룹핑 (wafer별 / lot별 / split factor별) ───────
    def auto_group(self, mode: str | None = None,
                   lots: list[str] | None = None) -> int:
        """조회 범위를 자동 배정. 만든 그룹 수를 반환.

        재실행해도 결과가 같도록(멱등) 기존 손배정을 먼저 지운다. 그룹 이름은
        wafer ID(또는 lot ID)를 그대로 쓰고, 첫 그룹은 기존 `_add_group` 규칙대로
        REF가 된다. [적용] 전에도 색인(wafer_index)만으로 동작한다.

        `lots`를 주면 **그 lot만** 대상으로 하고, 다른 lot에 짜 둔 배정과 그룹은
        건드리지 않는다(멀티 lot 탭) — lot을 갈아 가며 작업할 수 있어야 한다.
        `lots`를 주지 않으면 색인 전체를 처음부터 다시 만든다(단일 lot 탭의
        지금까지 동작 그대로).
        """
        from etreport.model.specs import GroupStyle

        st = self.state
        mode = mode or self.auto_mode()
        if self.index.is_empty():
            return 0
        if mode == "factor":
            return self._auto_group_factor(lots)
        idx = (self.index if lots is None
               else self.index.filter(pl.col("lot").is_in(lots)))
        if idx.is_empty():
            return 0
        keys = (["lot"] if mode == "lot" else ["lot", "wafer"])
        # DB 조회 순서는 보장되지 않는다 — lot·wafer 순으로 정렬해 그룹 번호와
        # 색이 실행할 때마다 달라지지 않게 한다
        rows = idx.select(keys).unique().sort(keys).iter_rows(named=True)
        kept = self._clear_scope(lots)         # 멱등 — 이 범위는 처음부터
        made = 0
        for i, rec in enumerate(rows):
            lot = rec["lot"]
            # 멀티 lot 탭에서는 wafer ID만으로 어느 lot인지 알 수 없어 lot을
            # 앞에 붙인다. 단일 lot 탭(lots=None)은 지금까지의 이름 그대로 둔다.
            name = lot if mode == "lot" else (
                f"{lot}·{rec['wafer']}" if lots is not None else rec["wafer"])
            n = kept + i                       # 남아 있는 그룹 뒤에 이어 붙인다
            gid = self._free_gid("a")
            st.groups.append(GroupStyle(
                gid=gid, name=name,
                color=REF_COLOR if n == 0 else PALETTE_OKABE[n % len(PALETTE_OKABE)],
                symbol="d" if n == 0 else SYMBOLS[n % len(SYMBOLS)],
                ref=n == 0))
            members = (idx.filter(pl.col("lot") == lot) if mode == "lot"
                       else idx.filter((pl.col("lot") == lot)
                                       & (pl.col("wafer") == rec["wafer"])))
            for w in dict.fromkeys(members["wafer"].to_list()):
                st.manual_groups[(lot, w, None, None, None)] = gid
            made += 1
        if st.data is not None:
            st.data = loader.apply_manual_groups(st.data, st.manual_groups)
        self._fill_groups()
        return made

    def _clear_scope(self, lots: list[str] | None) -> int:
        """자동 그룹핑을 다시 돌리기 전에 **그 범위만** 비운다. 남은 그룹 수 반환.

        `lots`가 None이면 전부 — 단일 lot 탭은 늘 색인 전체를 대상으로 하므로
        지금까지처럼 처음부터 다시 만든다. `lots`가 주어지면 그 lot의 배정만
        지우고, **그 lot들만 쓰던 그룹**을 함께 정리한다(아무도 안 쓰는 빈
        그룹이 목록에 남지 않게). 다른 lot의 그룹·배정은 그대로 둔다.

        프레임의 `gid`도 함께 비운다 — `manual_groups`만 지우면 다시 배정받지
        못한 wafer(예: 실험 조건표에 없는 wafer)가 예전 gid를 그대로 달고 있어,
        지운 그룹에 속한 것처럼 화면에 남는다.
        """
        st = self.state
        if lots is None:
            st.manual_groups.clear()
            st.groups = []
            self._clear_frame_gid(None)
            return 0
        scope = set(lots)
        for k in [k for k in st.manual_groups if k[0] in scope]:
            del st.manual_groups[k]
        still_used = set(st.manual_groups.values())
        st.groups = [g for g in st.groups if g.gid in still_used]
        self._clear_frame_gid(scope)
        return len(st.groups)

    def _clear_frame_gid(self, scope: set[str] | None) -> None:
        """프레임의 gid를 비운다. scope가 None이면 전부, 아니면 그 lot만."""
        st = self.state
        if st.data is None or "gid" not in st.data.columns:
            return
        blank = pl.lit("")
        expr = (blank if scope is None else
                pl.when(pl.col("lot").is_in(list(scope))).then(blank)
                .otherwise(pl.col("gid")))
        st.data = st.data.with_columns(expr.alias("gid"))

    def _free_gid(self, prefix: str) -> str:
        """쓰이지 않은 gid — 남겨 둔 그룹과 번호가 겹치면 배정이 뒤섞인다."""
        taken = {g.gid for g in self.state.groups}
        i = 0
        while f"{prefix}{i}" in taken:
            i += 1
        return f"{prefix}{i}"

    def _auto_group_factor(self, lots: list[str] | None = None) -> int:
        """실험 조건(split)의 배정을 **manual_groups로 굽는다**(§9.2).

        도크 [factor 편집]은 프레임의 gid를 직접 쓴다. 그래서 그 배정을 출발점
        삼아 몇 장 옮겨 두면 [적용]할 때 통째로 되돌아간다. 여기서 구워 두면
        loader가 실험 조건보다 **뒤에** manual_groups를 걸기 때문에 손으로 고친
        쪽이 이긴다 — "자동으로 짜 놓고 손보기"가 그제야 성립한다.

        조건 무관(전체 범위)으로 굽는다. factor는 wafer의 성질이지 측정 조건별로
        갈리는 것이 아니다.

        `lots`를 주면 그 lot의 배정만 다시 만든다 — 다른 lot을 이미 factor로
        묶어 뒀다면 gid가 같으므로(`styles_for`가 조합마다 같은 gid를 준다)
        두 번에 나눠 돌려도 한 그룹으로 합쳐진다.
        """
        st = self.state
        if st.split is None or not st.factors:
            return 0
        assign = st.split.assignment(st.factors)     # 정규화 키 → gid
        styles = st.split.styles_for(st.factors)
        rows = self._rows_for(lots if lots is not None
                              else sorted(set(self.index["lot"].to_list())))
        if rows.is_empty():
            return 0
        self._clear_scope(lots)                      # 멱등 — 이 범위만
        pairs = rows.select(["lot", "wafer"]).unique().sort(["lot", "wafer"])
        for lot, wf in pairs.iter_rows():
            gid = assign.get(wnorm.key(lot, wf))
            if gid is None:                          # 실험 조건표에 없는 wafer
                continue
            st.manual_groups[(lot, wf, None, None, None)] = gid
        # 남아 있던 그룹과 합쳐 **split이 정한 순서**로 다시 세운다. 자동 배정한
        # 그룹은 스타일도 split의 것을 쓴다(색·심볼 규칙을 두 곳에 두지 않는다).
        used = set(st.manual_groups.values())
        keep = [g for g in st.groups if g.gid in used
                and g.gid not in {s.gid for s in styles}]
        st.groups = [*keep, *[g for g in styles if g.gid in used]]
        if st.data is not None:
            st.data = loader.apply_manual_groups(st.data, st.manual_groups)
        self._fill_groups()
        return len([g for g in styles if g.gid in used])

    def auto_mode(self) -> str:
        return AUTO_MODES.get(self.cmb_auto.currentText(), "wafer")

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
                # 도크에서 고른 lot이 있으면 그 범위만 읽는다 — 그룹 편집이
                # 분석 대상과 다른 lot을 보여 주면 배정한 것이 화면에 안 나온다.
                self.index = loader.wafer_index_from_db(
                    self.db_path, list(getattr(st, "lots_selected", []) or []))
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
        if hasattr(self, "m_lots"):            # 멀티 lot 탭의 lot 목록도 새 DB로
            self._m_fill_lots()

    # ── 필터 (단일 lot·멀티 lot 공용) ────────────────────────
    def _selected_from(self, filters: dict[str, QComboBox],
                       upto: str | None = None) -> dict[str, str]:
        """콤보 묶음 → 조건. upto를 주면 **그 앞 단계까지만**.

        연쇄 필터의 핵심 — 뒤 콤보의 목록은 앞 단계로 좁힌 결과에서 뽑는다.
        값은 itemData에서 읽는다. 멀티 lot에서는 라벨에 `(2/3 lot)`이 붙어
        보이는 글자와 실제 값이 다르기 때문이다.
        """
        out: dict[str, str] = {}
        for _label, name in FILTERS:
            if name == upto:
                break
            cmb = filters.get(name)
            if cmb is not None and cmb.currentData() is not None:
                out[name] = cmb.currentData()
        return out

    def _selected(self, upto: str | None = None) -> dict[str, str]:
        return self._selected_from(self.filters, upto)

    def _rows_for(self, lots: list[str],
                  conds: dict[str, str] | None = None) -> pl.DataFrame:
        """lot 목록 + 조건으로 좁힌 색인. lot이 없으면 빈 결과."""
        if not lots or self.index.is_empty():
            return loader.wafer_index_empty()
        sub = self.index.filter(pl.col("lot").is_in(lots))
        for name, val in (conds or {}).items():
            sub = sub.filter(pl.col(name) == val)
        return sub

    def _rows(self, conds: dict[str, str] | None = None) -> pl.DataFrame:
        """단일 lot 탭의 조회 범위."""
        if self._lot is None:
            return loader.wafer_index_empty()
        return self._rows_for([self._lot],
                              self._selected() if conds is None else conds)

    def _refill_filters(self, filters: dict[str, QComboBox],
                        lots: list[str]) -> None:
        """앞 단계로 좁힌 결과에서 각 콤보의 목록을 다시 만든다.

        멀티 lot이면 **합집합**을 보여 주되 일부 lot에만 있는 값에는
        `(2/3 lot)`을 붙인다(§9.2). 교집합만 보여 주면 한 lot에만 걸린 조건을
        고를 길이 없어지고, 표시 없이 합집합만 보여 주면 조회가 비는 조합을
        만들게 된다. lot이 하나면 라벨은 값 그대로다.
        """
        for _label, name in FILTERS:
            cmb = filters[name]
            keep = cmb.currentData()
            sub = self._rows_for(lots, self._selected_from(filters, upto=name))
            seen: dict[str, set[str]] = {}
            for lot, val in zip(sub["lot"], sub[name]):
                if val is not None:
                    seen.setdefault(str(val), set()).add(lot)
            n_lots = len(set(sub["lot"].to_list())) or len(lots)
            cmb.blockSignals(True)               # 갱신 중 재귀 방지
            cmb.clear()
            cmb.addItem(ALL, None)
            for val in sorted(seen):
                k = len(seen[val])
                cmb.addItem(val if k >= n_lots else f"{val}  ({k}/{n_lots} lot)",
                            val)
            i = cmb.findData(keep)
            cmb.setCurrentIndex(i if i >= 0 else 0)
            cmb.setEnabled(bool(seen))
            cmb.blockSignals(False)

    def _refresh_filters(self) -> None:
        self._refill_filters(self.filters,
                             [self._lot] if self._lot else [])

    def _filter_changed(self, name: str) -> None:
        self._refresh_filters()
        self._update_counts()
        self._refresh_lists()

    def _count_text(self, sub: pl.DataFrame, lots: list[str]) -> str:
        pts = int(sub["n"].sum() or 0)
        n_waf = sub.select(["lot", "wafer"]).unique().height
        head = f"lot {len(lots)}개 · " if len(lots) > 1 else ""
        return f"{head}유효 {n_waf}장 · {pts:,}포인트"

    def _update_counts(self) -> None:
        if self._lot is None:
            return
        self.lbl_valid.setText(self._count_text(self._rows(), [self._lot]))

    # ── 그룹 목록 ────────────────────────────────────────────
    def _fill_groups(self) -> None:
        """두 탭의 그룹 콤보를 함께 채운다 — 그룹은 탭이 아니라 상태의 것이다."""
        for cmb in (self.cmb_group, getattr(self, "m_cmb_group", None)):
            if cmb is None:
                continue
            cmb.blockSignals(True)
            cur = cmb.currentIndex()
            cmb.clear()
            cmb.addItems([g.name for g in self.state.groups])
            if self.state.groups:
                cmb.setCurrentIndex(min(max(cur, 0), len(self.state.groups) - 1))
            cmb.blockSignals(False)
        self._refresh_lists()
        self._m_refresh_lists()

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

    # ── 배정 (단일 lot·멀티 lot 공용) ────────────────────────
    def _key_for(self, lot: str, wafer: str, conds: dict[str, str]) -> tuple:
        """배정 키 — 준 조건까지 포함한다(§9.1 '배정도 필터 범위에만').

        `conds`가 비어 있으면 `(lot, wafer, None, None, None)` = 조건 무관이다.
        멀티 lot 탭의 [현재 필터 범위에만 배정]이 꺼져 있을 때가 이 경우다.
        """
        return (lot, wafer, conds.get("step"), conds.get("temp"),
                conds.get("site"))

    def _key(self, wafer: str) -> tuple:
        return self._key_for(self._lot, wafer, self._selected())

    def _gid_for(self, lot: str, wafer: str, conds: dict[str, str]) -> str:
        """그 조건 범위에서 이 wafer가 어느 그룹인지."""
        st = self.state
        if st.data is not None and "gid" in st.data.columns:
            sub = st.data.filter((pl.col("lot") == lot)
                                 & (pl.col("wafer") == wafer))
            for name, val in conds.items():
                if name in sub.columns:
                    sub = sub.filter(pl.col(name).cast(pl.Utf8) == val)
            if not sub.is_empty():
                return sub["gid"][0]
        return st.manual_groups.get(self._key_for(lot, wafer, conds), "")

    def _gid_of(self, wafer: str) -> str:
        return self._gid_for(self._lot, wafer, self._selected())

    def _assign_pairs(self, pairs: list[tuple[str, str]], gid: str,
                      conds: dict[str, str]) -> None:
        """(lot, wafer) 쌍들을 그룹에 배정. [적용] 전에도 기록해 둔다."""
        st = self.state
        for lot, wf in pairs:
            st.manual_groups[self._key_for(lot, wf, conds)] = gid
        if st.data is not None:
            st.data = loader.apply_manual_groups(st.data, st.manual_groups)

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
        """단일 lot 탭의 배정 — **고른 필터 범위에만** 걸린다."""
        if self._lot is None:
            return
        self._assign_pairs([(self._lot, w) for w in wafers], gid,
                           self._selected())
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
        """조회 UI(위) + 접이식 붙여넣기(아래).

        단일 lot 탭과 **같은 헬퍼**를 쓴다 — 필터 연쇄·배정 키·자동 그룹핑의
        규칙이 두 벌이 되면 한쪽만 고쳐지는 버그가 반드시 생긴다. 다른 것은
        '몇 개의 lot을 보느냐'와 wafer를 `lot·wafer`로 가리킨다는 점뿐이다.
        """
        from etreport.ui.widgets.cards import CollapsibleSection

        w = QWidget()
        v = QVBoxLayout(w)

        top = QHBoxLayout()
        top.addWidget(QLabel("lot"))
        # lot이 수십 개인 DB에서는 74px 목록을 굴려 찾는 것이 일이다. 레일의
        # lot 절과 **같은 관용구**로 검색을 둔다 — 검색은 숨기기만 하고 체크는
        # 건드리지 않는다(걸러 놓고 배정했는데 안 보이던 lot이 조용히 빠지면 안 된다).
        lotbox = QVBoxLayout()
        lotbox.setContentsMargins(0, 0, 0, 0)
        self.m_search = QLineEdit()
        self.m_search.setPlaceholderText("lot 검색")
        self.m_search.setClearButtonEnabled(True)
        self.m_search.textChanged.connect(self._m_filter_lots)
        lotbox.addWidget(self.m_search)
        self.m_lots = QListWidget()
        self.m_lots.setSelectionMode(QListWidget.NoSelection)
        self.m_lots.setFixedHeight(74)
        self.m_lots.itemChanged.connect(self._m_lots_changed)
        lotbox.addWidget(self.m_lots)
        top.addLayout(lotbox, 1)
        side = QVBoxLayout()
        for text, on in (("전체", True), ("해제", False)):
            b = QPushButton(text)
            b.setProperty("ghost", True)
            b.setFixedWidth(52)
            b.clicked.connect(lambda _c=False, o=on: self._m_check_all(o))
            side.addWidget(b)
        side.addStretch(1)
        top.addLayout(side)
        v.addLayout(top)

        fl = QHBoxLayout()
        self.m_filters: dict[str, QComboBox] = {}
        for label, name in FILTERS:
            fl.addWidget(QLabel(label))
            cmb = QComboBox()
            cmb.setMinimumWidth(110)
            cmb.currentIndexChanged.connect(lambda _i: self._m_filter_changed())
            self.m_filters[name] = cmb
            fl.addWidget(cmb)
        fl.addStretch(1)
        fl.addWidget(QLabel("자동 그룹핑"))
        self.m_cmb_auto = QComboBox()
        self.m_cmb_auto.addItems(["lot별", "wafer별", "split factor별"])
        fl.addWidget(self.m_cmb_auto)
        b = QPushButton("실행")
        b.setToolTip("고른 lot을 자동으로 그룹에 배정합니다.\n"
                     "'split factor별'은 실험 조건이 만든 배정을 그대로 굽습니다 —\n"
                     "그 뒤에 손으로 옮긴 것은 [적용]해도 살아남습니다.")
        b.clicked.connect(self._m_auto_clicked)
        fl.addWidget(b)
        v.addLayout(fl)

        info = QHBoxLayout()
        self.m_lbl_valid = QLabel()
        self.m_lbl_valid.setObjectName("hint")
        info.addWidget(self.m_lbl_valid, 1)
        self.m_chk_range = QCheckBox("현재 필터 범위에만 배정")
        self.m_chk_range.setToolTip(
            "켜면 고른 step·site·온도에서만 그룹이 걸립니다.\n"
            "끄면 그 wafer의 모든 측정 조건에 걸립니다(붙여넣기와 같은 규칙).")
        info.addWidget(self.m_chk_range)
        v.addLayout(info)

        mid = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("미배정 wafer"))
        self.m_list_pool = QListWidget()
        self.m_list_pool.setSelectionMode(QListWidget.ExtendedSelection)
        left.addWidget(self.m_list_pool)
        mid.addLayout(left, 1)

        arrows = QVBoxLayout()
        arrows.addStretch(1)
        for text, fn in (("→", lambda: self._m_move(True)),
                         ("←", lambda: self._m_move(False)),
                         ("≫", lambda: self._m_move_all(True)),
                         ("≪", lambda: self._m_move_all(False))):
            btn = QPushButton(text)
            btn.setProperty("ghost", True)
            btn.setFixedWidth(46)
            btn.clicked.connect(fn)
            arrows.addWidget(btn)
        arrows.addStretch(1)
        mid.addLayout(arrows)

        right = QVBoxLayout()
        self.m_cmb_group = QComboBox()
        self.m_cmb_group.currentIndexChanged.connect(
            lambda _i: self._m_refresh_lists())
        right.addWidget(self.m_cmb_group)
        self.m_list_grp = QListWidget()
        self.m_list_grp.setSelectionMode(QListWidget.ExtendedSelection)
        right.addWidget(self.m_list_grp)
        mid.addLayout(right, 1)
        v.addLayout(mid, 1)

        # 붙여넣기 — 정식 입력 경로지만 기본은 접어 둔다(§9.2)
        paste_box = CollapsibleSection("엑셀에서 붙여넣기", collapsed=True)
        paste_box.body.addWidget(QLabel(
            "lot · wafer · group 표를 복사해 붙여넣으세요. 머리글은 있어도 없어도\n"
            "됩니다. wafer 열이 없으면 그 lot 전체가 한 그룹이 됩니다.\n"
            "붙여넣기는 **조건을 따지지 않습니다**(항상 전체 범위)."))
        self.paste = QPlainTextEdit()
        self.paste.setFixedHeight(96)
        self.paste.setPlaceholderText(
            "lot\twafer\tgroup\nPA123\tW01\tSplit_A\nPA123\tW02\tSplit_A")
        paste_box.body.addWidget(self.paste)
        b = QPushButton("붙여넣은 내용 적용")
        b.clicked.connect(self._apply_paste)
        paste_box.body.addWidget(b)
        v.addWidget(paste_box)

        self._fill_groups()          # 그룹 콤보는 두 탭이 함께 쓴다
        self._m_fill_lots()
        return w

    # ── 멀티 lot: lot 목록 ───────────────────────────────────
    def _m_fill_lots(self) -> None:
        """색인의 lot을 체크 리스트로. 도크에서 고른 lot이 있으면 그것만 켠다."""
        lots = sorted(set(self.index["lot"].to_list())) \
            if not self.index.is_empty() else []
        picked = set(getattr(self.state, "lots_selected", []) or []) or set(lots)
        self.m_lots.blockSignals(True)
        self.m_lots.clear()
        for lot in lots:
            it = QListWidgetItem(lot)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if lot in picked else Qt.Unchecked)
            self.m_lots.addItem(it)
        self.m_lots.blockSignals(False)
        self._m_filter_lots(self.m_search.text())
        self._m_filter_changed()

    def _m_selected_lots(self) -> list[str]:
        return [self.m_lots.item(i).text()
                for i in range(self.m_lots.count())
                if self.m_lots.item(i).checkState() == Qt.Checked]

    def _m_filter_lots(self, text: str) -> None:
        """검색어에 안 맞는 lot을 **숨기기만** 한다 — 체크 상태는 그대로."""
        q = text.strip().lower()
        for i in range(self.m_lots.count()):
            it = self.m_lots.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def _m_check_all(self, on: bool) -> None:
        """[전체]/[해제]는 **지금 보이는 lot에만** 건다 — 찾아 놓고 고르는 흐름."""
        self.m_lots.blockSignals(True)
        for i in range(self.m_lots.count()):
            it = self.m_lots.item(i)
            if not it.isHidden():
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.m_lots.blockSignals(False)
        self._m_filter_changed()

    def _m_lots_changed(self, _item) -> None:
        self._m_filter_changed()

    # ── 멀티 lot: 필터·리스트·배정 ───────────────────────────
    def _m_conds(self) -> dict[str, str]:
        """배정에 쓸 조건 — 체크박스가 꺼져 있으면 조건 무관(전체 범위)."""
        if not self.m_chk_range.isChecked():
            return {}
        return self._selected_from(self.m_filters)

    def _m_filter_changed(self) -> None:
        lots = self._m_selected_lots()
        self._refill_filters(self.m_filters, lots)
        self.m_lbl_valid.setText(self._count_text(
            self._rows_for(lots, self._selected_from(self.m_filters)), lots))
        self._m_refresh_lists()

    def _m_current_group(self):
        i = self.m_cmb_group.currentIndex()
        if 0 <= i < len(self.state.groups):
            return self.state.groups[i]
        return None

    def _m_refresh_lists(self) -> None:
        if not hasattr(self, "m_list_grp"):        # 초기화 중 호출 방어
            return
        self.m_list_pool.clear()
        self.m_list_grp.clear()
        group = self._m_current_group()
        gid = group.gid if group else ""
        conds = self._m_conds()
        rows = self._rows_for(self._m_selected_lots(),
                              self._selected_from(self.m_filters))
        if rows.is_empty():
            return
        pairs = rows.select(["lot", "wafer"]).unique().sort(["lot", "wafer"])
        for lot, wf in pairs.iter_rows():
            g = self._gid_for(lot, wf, conds)
            # lot이 여럿이므로 wafer ID만으로는 어느 wafer인지 가리킬 수 없다.
            # 보이는 글자와 별개로 (lot, wafer)를 데이터로 들고 다닌다.
            it = QListWidgetItem(f"{lot} · {wf}")
            it.setData(Qt.UserRole, (lot, wf))
            if group is not None and g == gid:
                it.setForeground(QColor(group.color))
                self.m_list_grp.addItem(it)
            elif not g:
                self.m_list_pool.addItem(it)

    def _m_assign(self, pairs: list[tuple[str, str]], gid: str) -> None:
        if pairs:
            self._assign_pairs(pairs, gid, self._m_conds())
            self._m_refresh_lists()

    def _m_move(self, to_group: bool) -> None:
        g = self._m_current_group()
        if to_group and g is None:
            QMessageBox.information(self, "그룹", "먼저 단일 lot 탭에서 ＋로 "
                                                "그룹을 만들거나 자동 그룹핑을 "
                                                "실행하세요")
            return
        src = self.m_list_pool if to_group else self.m_list_grp
        picked = [i.data(Qt.UserRole) for i in src.selectedItems()]
        self._m_assign(picked, g.gid if to_group else "")

    def _m_move_all(self, to_group: bool) -> None:
        g = self._m_current_group()
        if to_group and g is None:
            QMessageBox.information(self, "그룹", "먼저 그룹을 만드세요")
            return
        src = self.m_list_pool if to_group else self.m_list_grp
        allp = [src.item(i).data(Qt.UserRole) for i in range(src.count())]
        self._m_assign(allp, g.gid if to_group else "")

    def _m_auto_clicked(self) -> None:
        mode = AUTO_MODES.get(self.m_cmb_auto.currentText(), "lot")
        lots = self._m_selected_lots()
        if mode == "factor" and (self.state.split is None
                                 or not self.state.factors):
            QMessageBox.information(
                self, "자동 그룹핑",
                "실험 조건이 없습니다 — 도크에서 [실험 조건] 파일을 먼저 고르세요")
            return
        n = self.auto_group(mode, lots)
        self.m_lbl_valid.setText(f"{n}개 그룹 생성" if n
                                 else "배정할 wafer가 없습니다")
        self._m_refresh_lists()

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
        self._m_refresh_lists()

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
