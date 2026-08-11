"""분석 탭 3종이 공유하는 것 — 지연 계산 규약과 표 크기 맞추기.

**지연 계산 규약**: 표·plot·미리보기는 자동으로 다시 계산하지 않는다.
바뀐 게 있으면 버튼이 주황색(dirty)이 되고, 그 화면을 보고 있을 때만 자동으로
갱신된다. 세 탭이 각자 복붙하던 이 토글을 StaleMixin 하나로 모았다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QMessageBox, QTableWidget

ROW_H = 26


def pick_sheet(parent, path: str, what: str) -> str | int | None:
    """시트가 여럿이면 고르게 한다. 하나면 그대로, 취소하면 None.

    시트 목록 조회도 xlwings라 Excel 없는 환경에서는 첫 시트로 폴백.
    """
    try:
        from etreport.data.xlio import sheet_names
        names = sheet_names(path)
    except ImportError:
        return 0
    except Exception as e:                           # noqa: BLE001
        QMessageBox.critical(parent, what, f"파일을 열 수 없습니다:\n{e}")
        return None
    if len(names) <= 1:
        return names[0] if names else 0
    from PySide6.QtWidgets import QInputDialog
    name, ok = QInputDialog.getItem(
        parent, what, f"시트를 선택하세요 ({len(names)}개)", names, 0, False)
    return name if ok else None


def fit_table(t: QTableWidget) -> None:
    """표를 내용 크기에 딱 맞춰 — 잘림·회색 여백·스크롤바가 남지 않게."""
    t.resizeColumnsToContents()
    for c in range(t.columnCount()):
        t.setColumnWidth(c, t.columnWidth(c) + 12)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
    t.verticalHeader().setDefaultSectionSize(ROW_H)
    for r in range(t.rowCount()):
        t.setRowHeight(r, ROW_H)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    hh = max(t.horizontalHeader().sizeHint().height(), 34)
    t.setMaximumWidth(sum(t.columnWidth(c)
                          for c in range(t.columnCount())) + 4)
    t.setFixedHeight(hh + ROW_H * t.rowCount() + 6)


def set_dirty(button, on: bool) -> None:
    """버튼의 dirty 상태 전환 (style.qss가 주황색으로 그린다)."""
    button.setProperty("dirty", "true" if on else "false")
    button.style().unpolish(button)
    button.style().polish(button)


class StaleMixin:
    """계산이 밀렸음을 표시하고, 보고 있는 화면만 자동 갱신한다.

    쓰는 쪽에서 정하는 것:
      stale_button_attr  — 주황색으로 바뀔 버튼 속성 이름
      stale_label_attr   — 안내 문구를 쓸 QLabel 속성 이름 (없으면 None)
      stale_message      — 그 문구
      auto_refresh       — 보고 있을 때 자동으로 refresh()까지 할지
                           (Summary는 [표 만들기]를 눌러야만 계산한다)
    그리고 refresh()를 구현한다.
    """

    stale_button_attr: str = "btn_draw"
    stale_label_attr: str | None = None
    stale_message: str = "변경됨"
    auto_refresh: bool = True

    def init_stale(self) -> None:
        self._stale = True

    # ── 상태 ─────────────────────────────────────────────────
    def mark_stale(self) -> None:
        self._stale = True
        set_dirty(getattr(self, self.stale_button_attr), True)
        if self.stale_label_attr:
            getattr(self, self.stale_label_attr).setText(self.stale_message)
        if self.auto_refresh and self.isVisible():
            self.refresh()

    def mark_fresh(self) -> None:
        self._stale = False
        set_dirty(getattr(self, self.stale_button_attr), False)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if getattr(self, "_stale", True):
            self.refresh()

    def refresh(self) -> None:
        raise NotImplementedError
