"""item_id 확인 — 리포메터 REAL item ↔ 실제 추출 item 대조 다이얼로그.

실제 item은 caller가 모아서 넘긴다(적재된 DuckDB 컬럼 또는 bdq 프로브).
이 모듈은 순수 대조 함수 `diff_items` 와 그 결과를 보여주는 QDialog만 담당.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)


def diff_items(rf_items: list[str] | None,
               actual_items: list[str] | None) -> tuple[list[str], list[str]]:
    """리포메터에만 있는 item / 실제에만 있는 item 을 각각 반환.

    rf_items=None 이면 리포메터 목록 없음, actual_items=None 이면 실제 조회
    실패로 간주 — 둘 다 빈 집합으로 처리한다(다이얼로그가 별도 안내).
    """
    rf = set(rf_items or [])
    ac = set(actual_items or [])
    return (sorted(rf - ac), sorted(ac - rf))


class ItemCheckDialog(QDialog):
    """리포메터 REAL item 과 실제 item 을 양옆 리스트로 대조 표시."""

    def __init__(self, rf_items: list[str], actual_items: list[str] | None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("item_id 확인 — 리포메터 ↔ 실제")
        self.resize(720, 420)

        rf_only, ac_only = diff_items(rf_items, actual_items)

        lay = QVBoxLayout(self)
        if actual_items is None:
            lay.addWidget(QLabel(
                "실제 item을 가져올 수 없습니다 (DuckDB 미연결 또는 bdq 미설치). "
                "리포메터 REAL item만 표시합니다."))
        info = (f"리포메터 REAL: {len(rf_items)}개 · "
                f"실제: {len(actual_items) if actual_items is not None else '?'}개 · "
                f"리포메터만: {len(rf_only)} · 실제만: {len(ac_only)}")
        lay.addWidget(QLabel(info))

        hl = QHBoxLayout()
        lc = QVBoxLayout()
        lc.addWidget(QLabel("리포메터에만 있음 (데이터 없음)"))
        self.l_rf = QListWidget()
        self.l_rf.addItems(rf_only)
        lc.addWidget(self.l_rf)
        hl.addLayout(lc)

        rc = QVBoxLayout()
        rc.addWidget(QLabel("실제에만 있음 (리포메터 비맵핑)"))
        self.l_ac = QListWidget()
        self.l_ac.addItems(ac_only)
        rc.addWidget(self.l_ac)
        hl.addLayout(rc)
        lay.addLayout(hl)

        b = QPushButton("닫기")
        b.clicked.connect(self.accept)
        lay.addWidget(b)
