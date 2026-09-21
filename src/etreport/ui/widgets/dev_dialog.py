"""개발자 모드 — 비밀번호를 넣어야 바꿀 수 있는 내부 설정 창.

일반 사용자가 실수로 건드리면 추출이 느려지거나 결과가 달라지는 값만 여기에
둔다. 잠금은 보안 장치가 아니라 **오조작 방지**다(설정은 그대로 평문
settings.json에 남는다) — 그래서 해시를 씌우지 않고 상수 하나로 둔다.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

DEV_PASSWORD = "7879"
MAX_CHUNK_DAYS = 31


class DevDialog(QDialog):
    """`preset`의 개발자 설정을 고친다. [확인]을 눌러야 반영된다."""

    def __init__(self, preset, parent=None) -> None:
        super().__init__(parent)
        self.preset = preset
        self.setWindowTitle("개발자 모드")
        self.resize(460, 220)

        v = QVBoxLayout(self)
        head = QLabel(
            f"<b>{preset.name}</b> 설정의 내부 값입니다.<br>"
            "비밀번호를 넣어야 바꿀 수 있습니다.")
        head.setWordWrap(True)
        v.addWidget(head)

        pw = QHBoxLayout()
        pw.addWidget(QLabel("비밀번호"))
        self.ed_pw = QLineEdit()
        self.ed_pw.setEchoMode(QLineEdit.Password)
        self.ed_pw.returnPressed.connect(self._unlock)
        pw.addWidget(self.ed_pw, 1)
        self.btn_unlock = QPushButton("잠금 해제")
        self.btn_unlock.clicked.connect(self._unlock)
        pw.addWidget(self.btn_unlock)
        v.addLayout(pw)

        self.lbl_state = QLabel("잠김 — 값은 볼 수만 있습니다")
        self.lbl_state.setObjectName("hint")
        v.addWidget(self.lbl_state)

        row = QHBoxLayout()
        row.addWidget(QLabel("추출 청크 폭 (일)"))
        self.sp_days = QSpinBox()
        self.sp_days.setRange(1, MAX_CHUNK_DAYS)
        self.sp_days.setValue(max(1, int(getattr(preset, "chunk_days", 1) or 1)))
        self.sp_days.setToolTip(
            "기간을 며칠씩 묶어 한 쿼리로 조회할지. 기본 1일.\n"
            "lot 조건이 좁아 하루치가 가벼우면 2~3일로 늘려 쿼리 왕복을 줄입니다.\n"
            "넓히면 한 쿼리가 들고 오는 행 수도 그만큼 늘어납니다.")
        row.addWidget(self.sp_days)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)

        self.box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        v.addWidget(self.box)

        self._set_locked(True)

    # ── 잠금 ─────────────────────────────────────────────────
    def _set_locked(self, locked: bool) -> None:
        self.locked = locked
        self.sp_days.setEnabled(not locked)
        self.box.button(QDialogButtonBox.Ok).setEnabled(not locked)

    def _unlock(self) -> None:
        if self.ed_pw.text() != DEV_PASSWORD:
            self.lbl_state.setText("비밀번호가 다릅니다")
            return
        self._set_locked(False)
        self.ed_pw.setEnabled(False)
        self.btn_unlock.setEnabled(False)
        self.lbl_state.setText("해제됨 — 값을 바꿀 수 있습니다")

    # ── 반영 ─────────────────────────────────────────────────
    def accept(self) -> None:
        if not self.locked:
            self.preset.chunk_days = self.sp_days.value()
        super().accept()
