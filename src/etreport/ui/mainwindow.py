"""메인 창 — 상단 [데이터 | 분석] 전환 + QStackedWidget."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from etreport import APP_NAME, __version__
from etreport.config.catalog import Catalog
from etreport.config.settings import Settings
from etreport.model.state import AppState, StateBus
from etreport.ui.analysis_ws import AnalysisWorkspace
from etreport.ui.data_ws import DataWorkspace

HELP_TEXT = """파일 4종은 이렇게 맞물립니다.

  리포메터  (ITEMID → ALIAS · 단위 · 규격 · 계산식)
     ↑ ALIAS로 참조
  plot 템플릿  ─┐
                ├─ Report 열로 짝을 이룹니다
  table 템플릿 ─┘
  실험 조건 (lot·wafer별 step 조건 코드 — 선택)

· 리포메터의 ALIAS가 툴 안의 이름입니다. plot의 x·y와 table의 item_id는
  모두 이 ALIAS를 가리킵니다(ITEMID가 아닙니다).
· plot·table 템플릿은 Report 열 값이 같은 행끼리 한 리포트가 됩니다.
  한 파일에 여러 리포트를 담아 두고 템플릿에서 골라 쓰면 됩니다.
· 실험 조건은 없어도 됩니다 — 그룹은 [그룹 편집]에서 손으로 짤 수 있습니다.
  파일 대신 엑셀에서 복사해 붙여넣어도 됩니다.
· 표는 CAT1마다 한 장씩 자동으로 만들어집니다. plot 템플릿에 표 행을 둘
  필요가 없습니다.

[템플릿] 메뉴에서 4종 예시를 내려받으면 각 파일의 '설명' 시트에 컬럼 의미와
규칙이 정리돼 있습니다."""


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, catalog: Catalog,
                 state: AppState) -> None:
        super().__init__()
        self.settings, self.catalog, self.state = settings, catalog, state
        self.bus = StateBus()
        self.setWindowTitle(f"{APP_NAME}  v{__version__}")
        self.resize(1500, 940)

        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── 상단바 ───────────────────────────────────────────
        bar = QWidget()
        bar.setObjectName("topbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 8, 18, 8)
        h.setSpacing(10)
        logo = QWidget()                      # 강조는 색이므로 색은 QSS가 갖는다
        lg = QHBoxLayout(logo)
        lg.setContentsMargins(0, 0, 0, 0)
        lg.setSpacing(0)
        for text, name in (("ET ", "logo"), ("Report", "logoAccent")):
            lab = QLabel(text)
            lab.setObjectName(name)
            lg.addWidget(lab)
        h.addWidget(logo)
        h.addSpacing(14)
        self.ws_buttons: list[QPushButton] = []
        for i, name in enumerate(("데이터", "분석")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setObjectName("wsButton")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=i: self._switch(k))
            self.ws_buttons.append(b)
            h.addWidget(b)
        h.addStretch(1)
        self.db_pill = QLabel()
        self.db_pill.setObjectName("dbPill")
        h.addWidget(self.db_pill)
        v.addWidget(bar)

        # ── 스택 ─────────────────────────────────────────────
        self.stack = QStackedWidget()
        self.data_ws = DataWorkspace(settings, catalog, state, self.bus)
        self.anal_ws = AnalysisWorkspace(state, self.bus, settings)
        self.stack.addWidget(self.data_ws)
        self.stack.addWidget(self.anal_ws)
        v.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.data_ws.loaded.connect(self._after_load)
        self.bus.data_changed.connect(self._refresh_pill)

        self._build_menus()
        self._switch(1)          # 기본: 분석
        self._refresh_pill()

    # ── 상단 메뉴 (§11.4) ────────────────────────────────────
    def _build_menus(self) -> None:
        """[템플릿] 예시 내려받기 · [도움말] 네 파일 관계도."""
        from etreport.export import templates_sample as ts

        m = self.menuBar().addMenu("템플릿")
        for s in ts.all_samples():
            act = m.addAction(f"{s.title} 예시 저장…")
            act.triggered.connect(lambda _=False, k=s.key: self._save_sample(k))
        m.addSeparator()
        m.addAction("4종 한 파일로 저장…").triggered.connect(
            lambda: self._save_sample(None))

        h = self.menuBar().addMenu("도움말")
        h.addAction("사용 설명서 (PDF)").triggered.connect(self._open_manual)
        h.addAction("파일 4종 관계도").triggered.connect(self._show_help)
        h.addAction("버전").triggered.connect(
            lambda: QMessageBox.information(
                self, APP_NAME, f"{APP_NAME}  v{__version__}"))

    def _save_sample(self, key: str | None) -> None:
        from etreport.export import templates_sample as ts

        out = QFileDialog.getExistingDirectory(self, "예시를 저장할 폴더")
        if not out:
            return
        try:
            made = (ts.save_all(out) if key is None else
                    [ts.save_sample(next(s for s in ts.all_samples()
                                         if s.key == key), out)])
        except Exception as e:                    # noqa: BLE001 — 안내로 끝낸다
            QMessageBox.warning(self, "예시 저장", f"저장하지 못했습니다: {e}")
            return
        names = "\n".join(f"· {p.name}" for p in made)
        note = ("" if made[0].suffix == ".csv" else
                "\n\n같은 파일의 '… 설명' 시트에 컬럼 의미와 규칙이 정리돼 있습니다.")
        if made[0].suffix == ".csv":
            note = "\n\nExcel을 쓸 수 없어 CSV로 저장했습니다 (설명은 _설명.csv)."
        QMessageBox.information(self, "예시 저장", f"{out}\n\n{names}{note}")

    def _open_manual(self) -> None:
        """함께 배포된 사용 설명서 PDF를 기본 뷰어로 연다.

        배포본에 파일이 없을 수도 있으므로(개발 트리에서 아직 안 만든 경우)
        그때는 만드는 방법을 알려 준다 — 앱이 죽지 않는다.
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from etreport.export.manual import manual_path
        pdf = manual_path()
        if not pdf.exists():
            QMessageBox.information(
                self, "사용 설명서",
                "설명서 파일이 없습니다.\n\n"
                "개발 트리에서는 다음으로 만들 수 있습니다:\n"
                "  myenv/bin/python tools/make_manual.py")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf))):
            QMessageBox.information(self, "사용 설명서", str(pdf))

    def _show_help(self) -> None:
        QMessageBox.information(self, "파일 4종 관계도", HELP_TEXT)

    def closeEvent(self, e) -> None:
        """종료 정리 — 실행 중인 추출 스레드와 열려 있는 DB 연결을 닫는다."""
        from etreport.data.loader import close_store

        self.data_ws.shutdown()
        close_store(self.state)
        super().closeEvent(e)

    def _after_load(self) -> None:
        """추출·적재가 끝나면 같은 DB를 분석에 연결하고 화면 전환."""
        path = self.data_ws.preset().db_path
        if path:
            self.anal_ws.connect_db(path)
        self._switch(1)

    def _switch(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.ws_buttons):
            b.setChecked(i == idx)

    def _refresh_pill(self) -> None:
        st = self.state
        n = 0 if st.data is None else st.data.height
        n_item = len(st.aliases()) or (
            0 if st.data is None else
            len([c for c in st.data.columns
                 if c not in ("key", "lot", "wafer", "gid")]))
        self.db_pill.setText(
            f"{st.db_label}   ·   item {n_item}   ·   포인트 {n:,}")
