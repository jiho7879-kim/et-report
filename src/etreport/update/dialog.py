"""새 버전 안내 창 — [지금 업데이트] [나중에] [이 버전 건너뛰기].

앱 시작 시 백그라운드에서 check_for_update()를 돌리고,
결과가 있으면 이 다이얼로그를 띄운다. 강제하지 않는다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from etreport import __version__
from etreport.paths import update_tmp_dir
from etreport.update import apply as upd_apply
from etreport.update import checker


class _CheckThread(QThread):
    found = Signal(object)          # UpdateInfo | None

    def run(self) -> None:
        self.found.emit(checker.check_for_update())


class _DownloadThread(QThread):
    progress = Signal(int, int)
    done = Signal(object)           # Path | Exception

    def __init__(self, info: checker.UpdateInfo) -> None:
        super().__init__()
        self.info = info

    def run(self) -> None:
        dest = update_tmp_dir() / self.info.asset_name
        try:
            checker.download(self.info, dest, self.progress.emit)
            self.done.emit(dest)
        except Exception as e:                     # noqa: BLE001 — UI로 전달
            self.done.emit(e)


class UpdateDialog(QDialog):
    """업데이트 여부는 항상 사용자가 선택한다."""

    def __init__(self, info: checker.UpdateInfo, settings, parent=None) -> None:
        super().__init__(parent)
        self.info = info
        self.settings = settings
        self.setWindowTitle("ET Report 업데이트")
        self.setMinimumWidth(520)

        lay = QVBoxLayout(self)
        head = QLabel(
            f"<b style='font-size:15px'>새 버전이 있습니다</b><br>"
            f"현재 v{__version__}  →  <b>{info.tag}</b>"
        )
        head.setTextFormat(Qt.RichText)
        lay.addWidget(head)

        notes = QTextBrowser()
        notes.setMarkdown(info.notes)
        notes.setMaximumHeight(220)
        lay.addWidget(notes)

        self.bar = QProgressBar()
        self.bar.setVisible(False)
        lay.addWidget(self.bar)

        row = QHBoxLayout()
        self.btn_skip = QPushButton("이 버전 건너뛰기")
        self.btn_later = QPushButton("나중에")
        self.btn_go = QPushButton("지금 업데이트")
        self.btn_go.setDefault(True)
        row.addWidget(self.btn_skip)
        row.addStretch(1)
        row.addWidget(self.btn_later)
        row.addWidget(self.btn_go)
        lay.addLayout(row)

        self.btn_later.clicked.connect(self.reject)
        self.btn_skip.clicked.connect(self._skip)
        self.btn_go.clicked.connect(self._start_download)

    # ──────────────────────────────────────────────────────────
    def _skip(self) -> None:
        self.settings.skipped_version = self.info.version
        self.settings.save()
        self.reject()

    def _start_download(self) -> None:
        for b in (self.btn_go, self.btn_later, self.btn_skip):
            b.setEnabled(False)
        self.bar.setVisible(True)
        self._dl = _DownloadThread(self.info)
        self._dl.progress.connect(self._on_progress)
        self._dl.done.connect(self._on_done)
        self._dl.start()

    def _on_progress(self, done: int, total: int) -> None:
        self.bar.setMaximum(max(total, 1))
        self.bar.setValue(done)

    def _on_done(self, result) -> None:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if isinstance(result, Exception):
            self._fail(f"다운로드 실패: {result}")
            return
        try:
            # 자산이 단일 exe면 파일 하나를 덮어쓰고, zip이면 풀어서 폴더째 붓는다.
            kind, source = upd_apply.plan(result)
            upd_apply.apply_and_restart(source, kind)
        except upd_apply.UpdateNotApplicable as e:
            # 소스 실행 등 — 교체할 설치 폴더가 없다. 절대 진행하지 않는다.
            QMessageBox.information(self, "업데이트", str(e))
            self._fail("적용하지 않았습니다")
            return
        except Exception as e:                       # noqa: BLE001 — UI로 전달
            QMessageBox.critical(self, "업데이트 실패",
                                 f"새 버전을 적용하지 못했습니다:\n{e}")
            self._fail("적용 실패")
            return
        # 다이얼로그를 닫고 앱을 종료하면 배치가 폴더를 교체하고 재시작한다.
        QApplication.instance().quit()

    def _fail(self, msg: str) -> None:
        self.bar.setFormat(msg)
        for b in (self.btn_go, self.btn_later, self.btn_skip):
            b.setEnabled(True)


def check_async(parent, settings) -> None:
    """앱 시작 후 호출. 새 버전이 있고 건너뛴 버전이 아니면 창을 띄운다."""
    th = _CheckThread(parent)

    def _on_found(info) -> None:
        if info is None:
            return
        if info.version == getattr(settings, "skipped_version", None):
            return
        UpdateDialog(info, settings, parent).exec()

    th.found.connect(_on_found)
    th.finished.connect(th.deleteLater)
    th.start()
    parent._update_check_thread = th   # GC 방지
