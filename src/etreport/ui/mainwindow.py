"""메인 창 — 상단 [데이터 | 분석] 전환 + QStackedWidget."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
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

        self._switch(1)          # 기본: 분석
        self._refresh_pill()

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
