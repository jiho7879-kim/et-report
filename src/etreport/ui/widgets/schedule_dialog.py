"""추출 예약 창 — 프리셋 하나를 정해진 시각에 자동으로 돌린다(§13).

여기서 하는 일은 Windows 작업 스케줄러에 등록·해제하는 것뿐이다. 왜 상주
프로세스가 아니라 OS 스케줄러인지, 무엇이 안 되는지는 `etreport/schedule.py`
머리말에 적어 뒀다 — 창에서도 **되는 것과 안 되는 것을 그대로 보여 준다**.
"예약해 뒀는데 안 돌았다"가 가장 나쁜 결과라, 조건을 숨기지 않는다.
"""
from __future__ import annotations

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
)

from etreport import schedule as sched
from etreport.ui.widgets.cards import GhostButton


class ScheduleDialog(QDialog):
    def __init__(self, preset, parent=None) -> None:
        super().__init__(parent)
        self.preset = preset
        self.setWindowTitle(f"예약 실행 — {preset.name}")
        self.resize(620, 520)

        v = QVBoxLayout(self)
        ok, why = sched.available()
        head = QLabel(
            f"<b>{preset.name}</b> 프리셋으로 추출·적재를 자동 실행합니다.<br>"
            "앱이 꺼져 있어도 Windows 작업 스케줄러가 실행합니다 — "
            "단 <b>PC가 켜져 있고 그 계정으로 로그인</b>해 있어야 합니다"
            "(사내망 자격과 Excel이 계정에 묶여 있습니다).")
        head.setWordWrap(True)
        v.addWidget(head)
        if why:
            warn = QLabel(why)
            warn.setObjectName("warnBox")
            warn.setProperty("level", "warn" if ok else "err")
            warn.setWordWrap(True)
            v.addWidget(warn)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("주기"))
        self.cmb_freq = QComboBox()
        for key, label in sched.FREQ_LABELS.items():
            self.cmb_freq.addItem(label, key)
        self.cmb_freq.currentIndexChanged.connect(self._sync)
        r1.addWidget(self.cmb_freq)
        r1.addWidget(QLabel("시각"))
        self.tm = QTimeEdit()
        self.tm.setDisplayFormat("HH:mm")
        r1.addWidget(self.tm)
        r1.addWidget(QLabel("간격(시간)"))
        self.sp_interval = QSpinBox()
        self.sp_interval.setRange(1, 24)
        self.sp_interval.setValue(6)
        r1.addWidget(self.sp_interval)
        r1.addStretch(1)
        v.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("추출 기간"))
        self.sp_days = QSpinBox()
        self.sp_days.setRange(1, 90)
        self.sp_days.setSuffix(" 일")
        self.sp_days.setToolTip(
            "실행하는 날을 포함해 최근 며칠을 추출할지.\n"
            "1이면 그날치만. 하루 걸러 실패해도 메우고 싶으면 2~3으로 둡니다\n"
            "(같은 행은 key_hash로 걸러지므로 겹쳐 돌아도 중복되지 않습니다).")
        r2.addWidget(self.sp_days)
        r2.addStretch(1)
        v.addLayout(r2)

        v.addWidget(QLabel("등록될 명령"))
        self.cmd = QPlainTextEdit()
        self.cmd.setReadOnly(True)
        self.cmd.setObjectName("sqlBox")
        self.cmd.setFixedHeight(70)
        v.addWidget(self.cmd)

        self.lbl_jobs = QLabel()
        self.lbl_jobs.setObjectName("hint")
        self.lbl_jobs.setWordWrap(True)
        v.addWidget(self.lbl_jobs)
        v.addStretch(1)

        btns = QHBoxLayout()
        self.btn_reg = GhostButton("등록 · 갱신")
        self.btn_reg.clicked.connect(self._register)
        self.btn_del = GhostButton("예약 해제")
        self.btn_del.clicked.connect(self._unregister)
        btns.addWidget(self.btn_reg)
        btns.addWidget(self.btn_del)
        btns.addStretch(1)
        v.addLayout(btns)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

        self._load()
        for w in (self.btn_reg, self.btn_del):
            w.setEnabled(ok)

    # ── 상태 ─────────────────────────────────────────────────
    def _load(self) -> None:
        p = self.preset
        idx = self.cmb_freq.findData(getattr(p, "schedule_freq", sched.FREQ_DAILY))
        if idx >= 0:
            self.cmb_freq.setCurrentIndex(idx)
        hh, _, mm = str(getattr(p, "schedule_at", "06:00")).partition(":")
        self.tm.setTime(QTime(int(hh or 6), int(mm or 0)))
        self.sp_days.setValue(int(getattr(p, "schedule_days", 1) or 1))
        self.sp_interval.setValue(int(getattr(p, "schedule_interval", 6) or 6))
        self._sync()

    def job(self) -> sched.Job:
        return sched.Job(
            preset=self.preset.name,
            freq=self.cmb_freq.currentData() or sched.FREQ_DAILY,
            at=self.tm.time().toString("HH:mm"),
            days=self.sp_days.value(),
            interval=self.sp_interval.value())

    def _sync(self) -> None:
        hourly = self.cmb_freq.currentData() == sched.FREQ_HOURLY
        self.tm.setEnabled(not hourly)
        self.sp_interval.setEnabled(hourly)
        self.cmd.setPlainText(sched.command_line(self.job()))
        jobs = sched.list_jobs()
        mine = f"{sched.PREFIX}{self.preset.name}"
        self.lbl_jobs.setText(
            ("이 프리셋은 예약돼 있습니다. " if mine in jobs
             else "이 프리셋은 아직 예약되지 않았습니다. ")
            + (f"등록된 예약 {len(jobs)}개: {', '.join(jobs)}" if jobs else ""))

    # ── 동작 ─────────────────────────────────────────────────
    def _register(self) -> None:
        job = self.job()
        try:
            sched.register(job)
        except sched.ScheduleUnavailable as e:
            QMessageBox.warning(self, "예약 실패", str(e))
            return
        p = self.preset
        p.schedule_enabled = True
        p.schedule_freq = job.freq
        p.schedule_at = job.at
        p.schedule_days = job.days
        p.schedule_interval = job.interval
        QMessageBox.information(
            self, "예약됨",
            f"{job.label()}\n\n작업 이름: {job.task_name}\n\n"
            "작업 스케줄러(taskschd.msc)에서 실행 이력을 볼 수 있습니다.")
        self._sync()

    def _unregister(self) -> None:
        try:
            sched.unregister(self.preset.name)
        except sched.ScheduleUnavailable as e:
            QMessageBox.warning(self, "해제 실패", str(e))
            return
        self.preset.schedule_enabled = False
        self._sync()
