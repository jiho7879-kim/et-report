"""데이터 워크스페이스 — 프리셋 · 대상 · 기간 · 조건 · 실행."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from etreport.config.catalog import Catalog
from etreport.config.settings import Condition, ExtractPreset, Settings
from etreport.data import querybuilder as qb
from etreport.data.querybuilder import (
    ConditionError,
    build_extract_sql,
    build_item_probe_sql,
)
from etreport.data.reformatter import load as rf_load
from etreport.model.state import AppState, StateBus
from etreport.ui.widgets.autocomplete import AutoCompleteEdit
from etreport.ui.widgets.cards import Card, GhostButton, row
from etreport.ui.widgets.item_check_dialog import ItemCheckDialog

log = logging.getLogger(__name__)


class _ExtractThread(QThread):
    """추출 → 리포메팅 → 적재. 각 단계를 로그와 진행률로 내보낸다."""

    log = Signal(str)                 # 한 줄 로그
    step = Signal(str, int, int)      # 단계명, 완료, 전체
    finished_ok = Signal(int, str)    # 적재 행수, 소요시간
    failed = Signal(str)

    def __init__(self, preset, d_from, d_to, catalog) -> None:
        super().__init__()
        self.p, self.d_from, self.d_to, self.catalog = preset, d_from, d_to, catalog

    def run(self) -> None:
        import time
        t0 = time.monotonic()
        try:
            import polars as pl

            from etreport.data import extractor
            from etreport.data.db import Store, pivot_and_load
            from etreport.data.reformatter import apply as rf_apply
            from etreport.data.reformatter import load as rf_load
            from etreport.paths import cleanup_staging, staging_dir

            # 1) 리포메터 -------------------------------------
            t = time.monotonic()
            self.step.emit("리포메터 읽는 중", 0, 0)
            sheet = self.p.reformatter_sheet or 0
            self.log.emit(f"리포메터 열기: {Path(self.p.reformatter_path).name}"
                          + (f" [{sheet}]" if isinstance(sheet, str) else ""))
            rf = rf_load(self.p.reformatter_path, sheet)
            if rf.errors:
                raise RuntimeError("리포메터를 쓸 수 없습니다:\n" +
                                   "\n".join(rf.report_lines()[:8]))
            self.log.emit(
                f"  REAL {len(rf.reals())} · ADDP {len(rf.addps())}"
                f"  ({time.monotonic() - t:.1f}초)")
            for w in rf.warnings[:20]:
                self.log.emit(f"  ⚠ {w.row}행 {w.alias}: {w.message}")
            if len(rf.warnings) > 20:
                self.log.emit(f"  ⚠ 외 {len(rf.warnings) - 20}건 더 제외")

            # 2) 추출 ------------------------------------------
            t = time.monotonic()
            chunks = extractor.plan_chunks(self.d_from, self.d_to)
            self.log.emit(f"추출 시작 — {self.d_from} ~ {self.d_to} "
                          f"· 청크 {len(chunks)}개 · 워커 {extractor.N_WORKERS}")
            self.step.emit("추출 중", 0, len(chunks))

            def on_prog(done: int, total: int, label: str) -> None:
                self.step.emit("추출 중", done, total)
                self.log.emit(f"  청크 {done}/{total} 완료  ({label})")

            rf_items = [r.itemid for r in rf.reals() if r.itemid]
            files = extractor.extract_to_parquet(
                self.p.conditions, self.d_from, self.d_to, self.catalog,
                staging_dir(), on_prog, self.isInterruptionRequested,
                item_ids=rf_items)
            if not files:
                raise RuntimeError("중지되었거나 결과가 없습니다")
            raw = sum(pl.scan_parquet(str(f)).select(pl.len())
                      .collect().item() for f in files)
            self.log.emit(f"추출 완료 — {raw:,}행 (long) "
                          f"· {time.monotonic() - t:.1f}초")

            # 3) 리포메팅 --------------------------------------
            t = time.monotonic()
            n_addp = len(rf.addps())
            self.log.emit(f"리포메팅 시작 — 파일 {len(files)}개 · ADDP {n_addp}개")
            self.step.emit("리포메팅 중", 0, len(files))
            done_rows = 0
            reformatted: list[Path] = []
            for i, f in enumerate(files, 1):
                ft = time.monotonic()
                src = pl.read_parquet(f)

                def prog(stage: str, d: int, tot: int, _i=i) -> None:
                    # ADDP가 많으면 어느 item에서 시간이 가는지 보이게
                    self.step.emit(f"리포메팅 {_i}/{len(files)} · {stage}",
                                   d, tot or 1)

                out = rf_apply(rf, src, on_progress=prog)
                # 원본(추출 결과)은 남긴다 — 추출이 가장 비싼 단계라, 리포메터를
                # 고쳐서 다시 돌릴 때 재추출 없이 이 파일만 다시 쓰면 된다.
                rf_file = f.with_name(f.stem + "_rf.parquet")
                out.write_parquet(rf_file)
                reformatted.append(rf_file)
                done_rows += out.height
                self.log.emit(
                    f"  파일 {i}/{len(files)}  {src.height:,}행 → {out.height:,}행"
                    f"  ({time.monotonic() - ft:.1f}초)")
            self.log.emit(f"리포메팅 완료 — {done_rows:,}행 "
                          f"· {time.monotonic() - t:.1f}초")

            # 4) 적재 ------------------------------------------
            t = time.monotonic()
            self.log.emit(f"DuckDB 적재: {Path(self.p.db_path).name}")
            self.step.emit("적재 중", 0, 100)
            last = [0.0]

            def load_prog(d: int, tot: int) -> None:
                self.step.emit("적재 중", d, tot)
                now = time.monotonic()
                if now - last[0] > 2.0 or d == tot:    # 2초마다 한 줄
                    last[0] = now
                    self.log.emit(f"  버킷 {d}/{tot}")

            # 적재 연결은 반드시 닫는다 — 열려 있으면 DuckDB 쓰기 잠금이 남아
            # 곧바로 이어지는 [분석] 자동 연결(읽기 전용 열기)이 실패한다.
            store = Store(self.p.db_path)
            try:
                n = pivot_and_load(store, reformatted, on_progress=load_prog)
            finally:
                store.close()
            self.log.emit(f"적재 완료 — {n:,}행 · {time.monotonic() - t:.1f}초")

            # 5) 뒷정리 — staging은 놔두면 하루 수십 MB씩 쌓인다
            gone = cleanup_staging()
            if gone:
                self.log.emit(f"staging 정리 — 오래된 파일 {gone}개 삭제")

            el = time.monotonic() - t0
            self.log.emit(f"── 전체 {el:.1f}초 ──")
            self.finished_ok.emit(n, f"{el:.1f}초")
        except Exception as e:                      # noqa: BLE001 → UI로
            self.log.emit(f"실패: {e}")
            self.failed.emit(str(e))


class ConditionRow(QWidget):
    """컬럼 자동완성 + 타입 배지 + 타입별 값 힌트."""

    changed = Signal()
    removed = Signal(object)

    def __init__(self, cond: Condition, catalog: Catalog, parent=None) -> None:
        super().__init__(parent)
        self.cond, self.catalog = cond, catalog
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)

        self.ed_col = AutoCompleteEdit(
            lambda: [c.name for c in catalog.condition_columns()], comma=False)
        self.ed_col.setText(cond.col)
        self.ed_col.setFixedWidth(160)
        self.ed_col.setEnabled(not cond.required)
        self.ed_col.editingFinished.connect(self._col_changed)
        h.addWidget(self.ed_col)

        self.badge = QLabel()
        self.badge.setObjectName("typeBadge")
        self.badge.setFixedWidth(76)
        self.badge.setAlignment(Qt.AlignCenter)
        h.addWidget(self.badge)

        self.ed_val = QLineEdit(cond.val)
        self.ed_val.textEdited.connect(self._val_changed)
        h.addWidget(self.ed_val, 1)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["일반", "정규식"])
        self.cmb_mode.setCurrentIndex(1 if cond.mode == "regexp" else 0)
        self.cmb_mode.setFixedWidth(88)
        self.cmb_mode.currentIndexChanged.connect(self._mode_changed)
        h.addWidget(self.cmb_mode)

        if not cond.required:
            b = QPushButton("✕")
            b.setFixedWidth(30)
            b.setProperty("ghost", True)
            b.clicked.connect(lambda: self.removed.emit(self))
            h.addWidget(b)
        else:
            h.addSpacing(30)
        self._sync()

    def _col_changed(self) -> None:
        self.cond.col = self.ed_col.text().strip()
        self._sync()
        self.changed.emit()

    def _val_changed(self, t: str) -> None:
        self.cond.val = t
        self.changed.emit()

    def _mode_changed(self, i: int) -> None:
        self.cond.mode = "regexp" if i else "auto"
        self.changed.emit()

    def _sync(self) -> None:
        info = self.catalog.get(self.cond.col)
        t = info.dtype if info else "?"
        self.badge.setText(t)
        num = bool(info and info.is_numeric)
        ts = bool(info and info.is_timestamp)
        self.badge.setProperty("kind", "num" if num else "ts" if ts else "str")
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)
        self.cmb_mode.setEnabled(not (num or ts))
        self.ed_val.setPlaceholderText(
            "2026-08-01 ~ 2026-08-10" if ts else
            ">=25   ·   25 85   ·   25~85   ·   !0" if num else
            "PA12*  PB201  !PA125")


class DataWorkspace(QWidget):
    loaded = Signal()

    def __init__(self, settings: Settings, catalog: Catalog,
                 state: AppState, bus: StateBus, parent=None) -> None:
        super().__init__(parent)
        self.settings, self.catalog, self.state, self.bus = \
            settings, catalog, state, bus
        if not settings.extract_presets:
            settings.extract_presets.append(ExtractPreset(name="기본"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)
        outer.setAlignment(Qt.AlignHCenter)
        scroll.setWidget(host)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(scroll, 1)

        col = QWidget()
        col.setMaximumWidth(880)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        outer.addWidget(col)
        outer.addStretch(1)

        # 프리셋 ----------------------------------------------
        pc = Card("설정", "목적마다 DB·리포메터·조건을 묶어 저장합니다")
        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems([p.name for p in settings.extract_presets])
        self.cmb_preset.currentIndexChanged.connect(self._load_preset)
        b_save = GhostButton("저장")
        b_save.clicked.connect(lambda: settings.save())
        b_new = GhostButton("새 설정")
        b_new.clicked.connect(self._save_as)
        pc.body.addWidget(row(self.cmb_preset, b_save, b_new, stretch_at=0))
        v.addWidget(pc)

        # 대상 ------------------------------------------------
        tc = Card("대상")
        self.lbl_db = QLabel()
        self.lbl_rfm = QLabel()
        b1 = GhostButton("변경")
        b1.clicked.connect(self._pick_db)
        b2 = GhostButton("변경")
        b2.clicked.connect(self._pick_rfm)
        tc.body.addWidget(row("DuckDB", self.lbl_db, None, b1, stretch_at=1))
        tc.body.addWidget(row("리포메터", self.lbl_rfm, None, b2, stretch_at=1))
        self.lbl_cat = QLabel()
        b3 = GhostButton("새로고침")
        b3.clicked.connect(self._refresh_catalog)
        tc.body.addWidget(row("컬럼 정보", self.lbl_cat, None, b3, stretch_at=1))
        v.addWidget(tc)

        # 기간 ------------------------------------------------
        dc = Card("기간", "tkout_time 기준 · 파티션 컬럼이라 좁을수록 빠릅니다")
        self.d_from = QDateEdit(QDate.currentDate().addDays(-7))
        self.d_to = QDateEdit(QDate.currentDate())
        for d in (self.d_from, self.d_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
            d.dateChanged.connect(self._refresh_sql)
        q7 = GhostButton("최근 7일")
        q7.clicked.connect(lambda: self._quick(7))
        q30 = GhostButton("30일")
        q30.clicked.connect(lambda: self._quick(30))
        dc.body.addWidget(row(self.d_from, "—", self.d_to, q7, q30, None))
        v.addWidget(dc)

        # 조건 ------------------------------------------------
        cc = Card("조회 조건", "line_id는 파티션 컬럼이라 필수입니다")
        self.cond_host = QVBoxLayout()
        self.cond_host.setSpacing(6)
        cc.body.addLayout(self.cond_host)
        add = GhostButton("＋ 조건 추가")
        add.clicked.connect(self._add_cond)
        cc.body.addWidget(add)
        self.sql = QPlainTextEdit()
        self.sql.setReadOnly(True)
        self.sql.setObjectName("sqlBox")
        self.sql.setFixedHeight(128)
        cc.body.addWidget(QLabel("SQL 미리보기"))
        cc.body.addWidget(self.sql)
        b_item = GhostButton("item_id 확인")
        b_item.clicked.connect(self._open_item_check)
        cc.body.addWidget(b_item)
        v.addWidget(cc)

        # 실행 ------------------------------------------------
        rc = Card("실행")
        self.btn_run = QPushButton("추출하고 적재")
        self.btn_run.setMinimumWidth(210)          # 단계명이 길어도 안 잘리게
        self.btn_run.clicked.connect(self._run)
        self.btn_cancel = GhostButton("중지")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        self.chk_csv = QCheckBox("완료 후 CSV 저장")
        self.chk_csv.setChecked(True)
        rc.body.addWidget(row(self.btn_run, self.btn_cancel, self.chk_csv,
                              QCheckBox("SBDF"), None))
        self.lbl_step = QLabel("대기 중")
        self.lbl_step.setObjectName("hint")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        rc.body.addWidget(row(self.lbl_step, None))
        rc.body.addWidget(self.bar)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("logBox")
        self.log_view.setFixedHeight(170)
        b_copy = GhostButton("로그 복사")
        b_copy.clicked.connect(self._copy_log)
        b_clear = GhostButton("지우기")
        b_clear.clicked.connect(lambda: self.log_view.clear())
        rc.body.addWidget(row(QLabel("진행 로그"), None, b_copy, b_clear))
        rc.body.addWidget(self.log_view)
        self.run_card = rc

        # 실행/로그는 스크롤과 무관하게 항상 하단에 보이도록 고정
        foot = QWidget()
        foot.setObjectName("runFooter")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 10, 24, 14)
        inner = QWidget()
        inner.setMaximumWidth(880)
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.addWidget(self.run_card)
        fl.addStretch(1)
        fl.addWidget(inner, 10)
        fl.addStretch(1)
        lay.addWidget(foot)

        self._rows: list[ConditionRow] = []
        self._rf_items: list[str] = []   # 리포메터 REAL itemid (미리보기/필터/대조용)
        self._load_preset(0)

    # ── 프리셋 ───────────────────────────────────────────────
    def preset(self) -> ExtractPreset:
        return self.settings.extract_presets[self.cmb_preset.currentIndex()]

    def _load_preset(self, _i: int) -> None:
        p = self.preset()
        self.lbl_db.setText(p.db_path or "(미지정)")
        self.lbl_rfm.setText(p.reformatter_path or "(미지정)")
        while self.cond_host.count():
            it = self.cond_host.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._rows = []
        for c in p.conditions:
            self._add_row(c)
        self._refresh_catalog_label()
        self._load_rf_items()
        self._refresh_sql()

    def _save_as(self) -> None:
        name, ok = QInputDialog.getText(self, "새 설정", "이름")
        if ok and name:
            import copy
            p = copy.deepcopy(self.preset())
            p.name = name
            self.settings.extract_presets.append(p)
            self.cmb_preset.addItem(name)
            self.cmb_preset.setCurrentIndex(self.cmb_preset.count() - 1)
            self.settings.save()

    # ── 조건 ─────────────────────────────────────────────────
    def _add_row(self, cond: Condition) -> None:
        r = ConditionRow(cond, self.catalog)
        r.changed.connect(self._refresh_sql)
        r.removed.connect(self._remove_row)
        self._rows.append(r)
        self.cond_host.addWidget(r)

    def _add_cond(self) -> None:
        used = {c.col for c in self.preset().conditions}
        nxt = next((c.name for c in self.catalog.condition_columns()
                    if c.name not in used), "device_id")
        c = Condition(nxt)
        self.preset().conditions.append(c)
        self._add_row(c)
        self._refresh_sql()

    def _remove_row(self, r: ConditionRow) -> None:
        self.preset().conditions.remove(r.cond)
        self._rows.remove(r)
        r.deleteLater()
        self._refresh_sql()

    def _quick(self, n: int) -> None:
        self.d_to.setDate(QDate.currentDate())
        self.d_from.setDate(QDate.currentDate().addDays(-n + 1))

    def _refresh_sql(self) -> None:
        try:
            sql = build_extract_sql(
                self.preset().conditions, self.d_from.date().toPython(),
                self.d_to.date().toPython(), self.catalog,
                item_ids=self._rf_items)
            if self._rf_items:
                sql = sql + "\n" + qb._item_comment(self._rf_items)
            self.sql.setPlainText(sql)
        except ConditionError as e:
            self.sql.setPlainText(f"-- {e.col}: {e}")

    # ── 리포메터 item (필터/미리보기/대조) ───────────────────
    def _load_rf_items(self) -> None:
        """리포메터 REAL itemid 목록을 캐시 (xlsx 미설치 환경은 빈 목록)."""
        self._rf_items = []
        p = self.preset()
        if not p.reformatter_path:
            return
        try:
            rf = rf_load(p.reformatter_path, p.reformatter_sheet or 0)
        except Exception as e:            # noqa: BLE001 — xlwings 미설치 등
            log.warning("리포메터 item 목록 로드 실패(%s) — 필터 미적용", e)
            return
        self._rf_items = [r.itemid for r in rf.reals() if r.itemid]

    def _gather_actual_items(self) -> list[str] | None:
        """실제 item 목록 — 적재된 DuckDB 우선, 없으면 bdq 프로브."""
        p = self.preset()
        if p.db_path and Path(p.db_path).exists():
            try:
                import duckdb
                con = duckdb.connect(str(p.db_path), read_only=True)
                try:
                    cols = [r[0] for r in con.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='et_data'").fetchall()]
                finally:
                    con.close()
                keys = {"key_hash", "key", "lot", "wafer", "gid", "line_id",
                        "root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos",
                        "temperature", "step_id", "step_seq", "total_site_cnt",
                        "tkout_time"}
                return [c for c in cols if c.lower() not in keys]
            except Exception as e:            # noqa: BLE001
                log.warning("DB item 조회 실패(%s)", e)
        try:
            import bigdataquery as bdq  # 사내 패키지 — 지연 import
        except Exception:                       # noqa: BLE001 — 사내 PC 아님
            return None
        try:
            df = bdq.getData(build_item_probe_sql(
                p.conditions, self.d_from.date().toPython(),
                self.d_to.date().toPython(), self.catalog))
            import polars as pl
            return list(pl.from_pandas(df)["item_id"].unique().to_list())
        except Exception as e:            # noqa: BLE001
            log.warning("item 프로브 실패(%s)", e)
            return None

    def _open_item_check(self) -> None:
        actual = self._gather_actual_items()
        ItemCheckDialog(self._rf_items, actual, parent=self).exec()

    # ── 대상 ─────────────────────────────────────────────────
    def _pick_db(self) -> None:
        # 저장 다이얼로그 — 없는 파일명을 적으면 그대로 새 DB가 된다.
        # (기존 파일을 고르면 이어서 누적 적재. 덮어쓰기 확인은 뜨지만
        #  실제로 지우지 않고 열어서 씀)
        p, _ = QFileDialog.getSaveFileName(
            self, "DuckDB 선택 또는 새로 만들기", "et_data.duckdb",
            "DuckDB (*.duckdb)",
            options=QFileDialog.DontConfirmOverwrite)
        if p:
            if not p.lower().endswith(".duckdb"):
                p += ".duckdb"
            self.preset().db_path = p
            self.lbl_db.setText(p)

    def _pick_rfm(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "리포메터", "",
                                           "Excel (*.xlsx *.xlsm)")
        if not p:
            return
        from etreport.ui.analysis_ws import _pick_sheet
        sheet = _pick_sheet(self, p, "리포메터")
        if sheet is None:
            return
        self.preset().reformatter_path = p
        self.preset().reformatter_sheet = sheet if isinstance(sheet, str) else ""
        self.lbl_rfm.setText(p + (f"  [{sheet}]" if isinstance(sheet, str) else ""))
        self._load_rf_items()
        self._refresh_sql()

    def _refresh_catalog(self) -> None:
        try:
            self.catalog.refresh()
        except ModuleNotFoundError:
            QMessageBox.information(
                self, "컬럼 정보",
                "bigdataquery 미설치 — 캐시된 카탈로그를 사용합니다.\n"
                "사내 PC에서는 bdq.getColumnInfo('eds.f_et_test')를 재조회합니다.")
        self._refresh_catalog_label()

    def _refresh_catalog_label(self) -> None:
        self.lbl_cat.setText(
            f"{len(self.catalog.columns)}개 컬럼 · "
            f"{self.catalog.fetched_at or '캐시 없음'}")

    # ── 실행 ─────────────────────────────────────────────────
    def _run(self) -> None:
        p = self.preset()
        d_from, d_to = self.d_from.date().toPython(), self.d_to.date().toPython()
        if d_from > d_to:
            QMessageBox.warning(
                self, "기간 오류",
                f"시작일({d_from})이 종료일({d_to})보다 뒤입니다")
            return
        try:
            build_extract_sql(p.conditions, d_from, d_to, self.catalog,
                              item_ids=self._rf_items)
        except ConditionError as e:
            QMessageBox.warning(self, "조건 오류", f"{e.col}: {e}")
            return
        if not (p.db_path and p.reformatter_path):
            QMessageBox.warning(self, "설정 필요",
                                "DuckDB 파일과 리포메터를 먼저 지정하세요")
            return
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.log_view.clear()
        self._append(f"── {datetime.now():%H:%M:%S} 시작 ──")
        self._th = _ExtractThread(p, d_from, d_to, self.catalog)
        self._th.log.connect(self._append)
        self._th.step.connect(self._on_step)
        self._th.finished_ok.connect(self._done)
        self._th.failed.connect(self._fail)
        self._th.start()

    def shutdown(self) -> None:
        """앱 종료 시 추출 스레드를 정리한다 (MainWindow.closeEvent가 부른다).

        그냥 두면 QThread가 실행 중인 채로 파괴되어 프로세스가 죽는다
        (QThread: Destroyed while thread is still running). 이 위젯은
        QStackedWidget 안에 있어 자신의 closeEvent는 오지 않으므로,
        창 쪽에서 명시적으로 불러 줘야 한다.
        """
        th = getattr(self, "_th", None)
        if th is None or not th.isRunning():
            return
        th.requestInterruption()
        self._append("종료 중 — 진행 중인 청크가 끝나면 멈춥니다")
        if not th.wait(30_000):                  # 청크 하나가 끝날 때까지
            th.terminate()
            th.wait(2_000)

    def closeEvent(self, e) -> None:             # 단독 창으로 띄웠을 때 대비
        self.shutdown()
        super().closeEvent(e)

    def _append(self, line: str) -> None:
        self.log_view.appendPlainText(f"{datetime.now():%H:%M:%S}  {line}")
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_step(self, name: str, done: int, total: int) -> None:
        self.lbl_step.setText(
            f"{name}   {done}/{total}" if total else name)
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        else:
            self.bar.setRange(0, 0)          # 불확정 애니메이션
        self.btn_run.setText(name.split(" · ")[0])

    def _cancel(self) -> None:
        if getattr(self, "_th", None) and self._th.isRunning():
            self._th.requestInterruption()
            self._append("중지 요청 — 진행 중인 청크가 끝나면 멈춥니다")
            self.btn_cancel.setEnabled(False)

    def _reset_run_ui(self) -> None:
        self.btn_run.setEnabled(True)
        self.btn_run.setText("추출하고 적재")
        self.btn_cancel.setEnabled(False)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)

    def _done(self, n: int, elapsed: str) -> None:
        self._reset_run_ui()
        self.lbl_step.setText(f"완료 — {n:,}행 · {elapsed}")
        self.loaded.emit()

    def _fail(self, msg: str) -> None:
        self._reset_run_ui()
        self.lbl_step.setText("실패")
        QMessageBox.critical(self, "추출 실패", msg)

    def _copy_log(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.log_view.toPlainText())
