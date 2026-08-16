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
BDQ_MISSING = "bigdataquery가 없는 환경입니다 — 사내 PC에서 실행하세요"


def bdq_call(fn: Callable[[], object]) -> Callable[[], object]:
    """사내 조회(bdq)를 워커에 넘길 때 감싼다.

    `run_in_background`는 ImportError를 **Excel 없음**으로 안내한다. bdq 조회의
    ImportError는 의미가 다르므로 여기서 문구를 바꿔 올린다.
    """
    def wrapped() -> object:
        try:
            return fn()
        except ImportError as e:
            raise RuntimeError(BDQ_MISSING) from e
    return wrapped


class Worker(QThread):
    """fn()을 스레드에서 실행하고 결과(또는 예외)를 그대로 넘긴다.

    `wants_progress=True`면 fn에 **보고 함수 하나를 인자로 넘긴다** —
    `fn(report)`이고 `report(done, total, 라벨)`이다. 보고는 시그널로 나가므로
    워커 스레드에서 불러도 안전하다(위젯은 UI 스레드에서만 만진다).
    """

    finished_with = Signal(object)
    progressed = Signal(int, int, str)

    def __init__(self, fn: Callable[..., object], needs_com: bool = False,
                 parent=None, wants_progress: bool = False) -> None:
        super().__init__(parent)
        self._fn = fn
        self._needs_com = needs_com
        self._wants_progress = wants_progress

    def _report(self, done: int, total: int, label: str = "") -> None:
        self.progressed.emit(int(done), int(total), str(label))

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
            self.finished_with.emit(
                self._fn(self._report) if self._wants_progress else self._fn())
        except Exception as e:
            log.exception("백그라운드 작업 실패")
            self.finished_with.emit(e)
        finally:
            if com is not None:
                com.CoUninitialize()


def run_in_background(parent, title: str, fn: Callable[..., object],
                      done: Callable[[object], None] | None = None,
                      needs_com: bool = False,
                      with_progress: bool = False) -> Worker:
    """진행 창을 띄우고 fn을 워커에서 실행. 실패는 메시지 박스로 보여준다.

    반환된 Worker는 parent에 붙잡아 두므로 호출측이 따로 보관할 필요는 없다.

    `with_progress=True`면 fn은 인자 하나(보고 함수)를 받는다 —
    `fn(report)` · `report(done, total, 라벨)`. 진행 창이 도는 막대에서
    **확정 막대**로 바뀐다. PPT 생성처럼 수 분이 걸리는 작업에서 "멈춘 것 같다"는
    인상을 없애는 것이 목적이다. `total`이 0 이하면 **라벨만 바꾸고 막대는
    그대로 둔다** — 장수를 셀 수 없는 단계(집계·저장)를 알리는 데 쓴다.
    """
    dlg = QProgressDialog(f"{title} 중…", "", 0, 0, parent)
    dlg.setWindowTitle(title)
    dlg.setCancelButton(None)                  # 중간에 끊으면 파일이 반쯤 남는다
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.show()

    worker = Worker(fn, needs_com, parent, wants_progress=with_progress)

    def _on_progress(done_n: int, total: int, label: str) -> None:
        # 창이 이미 닫혔으면 조용히 무시한다(늦게 도착한 보고)
        if not dlg.isVisible():
            return
        if total > 0:
            if dlg.maximum() != total:
                dlg.setRange(0, total)
            dlg.setValue(done_n)
        dlg.setLabelText(f"{title} — {label}" if label else f"{title} 중…")

    worker.progressed.connect(_on_progress)

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
