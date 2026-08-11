"""오래 걸리는 작업을 워커 스레드로 — 창이 '응답 없음'이 되지 않게.

PPT 생성(수백 장의 matplotlib 렌더)과 xlsx 내보내기(Excel COM 왕복)는 데이터가
많아지면 수 분씩 걸린다. UI 스레드에서 돌리면 그동안 화면이 얼어붙고 진행
상황도, 취소도 없다.

Excel(xlwings)을 워커에서 쓰려면 그 스레드에서 COM을 초기화해야 한다
(xlwings 문서의 요구사항) — needs_com=True로 처리한다.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog

log = logging.getLogger(__name__)

EXCEL_MISSING = "xlwings/Excel이 없는 환경입니다 — 사내 PC에서 실행하세요"


class Worker(QThread):
    """fn()을 스레드에서 실행하고 결과(또는 예외)를 그대로 넘긴다."""

    finished_with = Signal(object)

    def __init__(self, fn: Callable[[], object], needs_com: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._needs_com = needs_com

    def run(self) -> None:
        com = None
        if self._needs_com:
            try:
                import pythoncom  # Windows에만 있다
                pythoncom.CoInitialize()
                com = pythoncom
            except ImportError:
                pass                           # 비 Windows — 어차피 xlwings도 없다
        try:
            self.finished_with.emit(self._fn())
        except Exception as e:
            log.exception("백그라운드 작업 실패")
            self.finished_with.emit(e)
        finally:
            if com is not None:
                com.CoUninitialize()


def run_in_background(parent, title: str, fn: Callable[[], object],
                      done: Callable[[object], None] | None = None,
                      needs_com: bool = False) -> Worker:
    """진행 창을 띄우고 fn을 워커에서 실행. 실패는 메시지 박스로 보여준다.

    반환된 Worker는 parent에 붙잡아 두므로 호출측이 따로 보관할 필요는 없다.
    """
    dlg = QProgressDialog(f"{title} 중…", "", 0, 0, parent)
    dlg.setWindowTitle(title)
    dlg.setCancelButton(None)                  # 중간에 끊으면 파일이 반쯤 남는다
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.show()

    worker = Worker(fn, needs_com, parent)

    def _on_done(result) -> None:
        dlg.close()
        holder = getattr(parent, "_bg_workers", None)
        if holder is not None and worker in holder:
            holder.remove(worker)
        if isinstance(result, ImportError):
            QMessageBox.warning(parent, title, EXCEL_MISSING)
            return
        if isinstance(result, Exception):
            QMessageBox.critical(parent, f"{title} 실패", str(result))
            return
        if done is not None:
            done(result)

    worker.finished_with.connect(_on_done)
    if not hasattr(parent, "_bg_workers"):
        parent._bg_workers = []                # GC 방지
    parent._bg_workers.append(worker)
    worker.start()
    return worker
