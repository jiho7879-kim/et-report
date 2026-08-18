"""추출 예약 실행 — **앱이 꺼져 있어도** 정해진 시각에 추출·적재가 돈다.

되는가? 된다. 앱 프로세스가 스스로 살아 있을 필요가 없다는 것이 요점이다.
파이프라인이 화면과 분리돼 있으므로(`data/pipeline.py`) exe를 **헤드리스 인자로**
한 번 띄우면 추출이 끝나고 스스로 종료한다. 그 "한 번 띄우기"를 반복해 주는
일은 OS가 이미 잘 한다 — Windows 작업 스케줄러(`schtasks`)에 등록한다.

    ETReport.exe --run-extract "야간 추출" --days 1

직접 서비스를 만들거나 상주 프로세스를 띄우지 않는 이유:
  - 상주 프로세스는 사용자가 로그아웃하면 죽고, 죽은 것을 아무도 모른다.
  - 작업 스케줄러는 재부팅·로그오프를 넘어 살아남고, 실패 이력·다음 실행 시각을
    OS가 관리해 준다. 우리가 다시 만들 이유가 없다.
  - bdq 조회는 **사용자 계정의 사내망 자격**으로 도므로 그 계정으로 돌아야 한다.
    그래서 SYSTEM 서비스가 아니라 사용자 작업으로 등록한다.

한계도 분명하다 — 정직하게 적어 둔다.
  - **Windows 전용**이다. 리눅스/WSL에서는 등록이 막힌다(cron을 쓰면 되지만,
    배포 대상이 사내 Windows PC라 두 갈래를 만들 이유가 없다).
  - PC가 꺼져 있으면 돌지 않는다. `--wake`로 절전 해제를 걸 수는 있지만
    전원이 꺼진 PC를 켜지는 못한다.
  - Excel(xlwings)이 필요한 리포메터를 읽으므로 **그 계정이 로그인해 있거나**
    Excel이 무인 실행 가능해야 한다. 그래서 기본은 `/IT`(로그인 상태에서만 실행)로
    등록한다 — 로그인 없이 돌리면 COM이 조용히 실패하는 편이 더 나쁘다.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: 작업 이름 앞에 붙는 접두사 — 우리가 만든 작업만 골라내고 지우기 위해서.
PREFIX = "ETReport_추출_"

#: 반복 주기.
FREQ_DAILY = "DAILY"
FREQ_WEEKLY = "WEEKLY"
FREQ_HOURLY = "HOURLY"
FREQ_LABELS = {FREQ_DAILY: "매일", FREQ_WEEKLY: "매주", FREQ_HOURLY: "몇 시간마다"}


class ScheduleUnavailable(RuntimeError):
    """예약을 걸 수 없는 환경 — 이유를 그대로 사용자에게 보여 준다."""


@dataclass
class Job:
    """등록된 예약 하나."""
    preset: str                 # ExtractPreset 이름
    freq: str = FREQ_DAILY
    at: str = "06:00"           # HH:MM
    days: int = 1               # 추출 기간 — '오늘 기준 며칠 전부터'
    interval: int = 6           # HOURLY일 때 몇 시간마다
    enabled: bool = True

    @property
    def task_name(self) -> str:
        return f"{PREFIX}{self.preset}"

    def label(self) -> str:
        when = (f"{self.interval}시간마다" if self.freq == FREQ_HOURLY
                else f"{FREQ_LABELS.get(self.freq, self.freq)} {self.at}")
        return f"{self.preset} — {when} · 최근 {self.days}일"


def available() -> tuple[bool, str]:
    """이 환경에서 예약을 걸 수 있는가. (가능 여부, 이유)."""
    if sys.platform != "win32":
        return False, ("예약 실행은 Windows에서만 됩니다 "
                       "(작업 스케줄러 schtasks를 씁니다).")
    if not getattr(sys, "frozen", False):
        return True, ("소스 실행 중입니다 — 예약은 배포된 exe 경로로 걸립니다. "
                      "지금 등록하면 파이썬 인터프리터 경로가 들어가므로, "
                      "배포본에서 다시 등록하세요.")
    return True, ""


def launcher() -> list[str]:
    """예약이 실행할 명령. 배포 exe면 exe 하나, 소스면 인터프리터 + -m.

    exe 경로에 공백이 흔하므로(`C:\\Program Files\\…`) 인용은 `command_line()`이
    한 번만 한다 — 두 곳에서 하면 따옴표가 겹쳐 schtasks가 경로를 못 찾는다.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "etreport"]


def command_line(job: Job) -> str:
    """`schtasks /TR`에 넘길 명령 문자열.

    schtasks는 /TR 값을 통째로 한 문자열로 받고 그 안의 인용을 스스로 해석한다.
    경로에 공백이 있을 수 있으므로 **경로만** 큰따옴표로 감싼다.
    """
    parts = [f'"{a}"' if " " in a else a for a in launcher()]
    parts += ["--run-extract", f'"{job.preset}"', "--days", str(job.days)]
    return " ".join(parts)


def register(job: Job) -> str:
    """작업 스케줄러에 등록(있으면 덮어쓴다). 돌려준 문자열은 실행한 명령.

    `/F`는 같은 이름이 있으면 덮어쓴다 — 예약을 고칠 때마다 지우고 다시 만들면
    그 사이에 실패하면 예약이 통째로 사라진다.
    `/IT`는 **로그인한 사용자로만** 실행한다. bdq 자격과 Excel COM이 그 계정에
    묶여 있어서, 로그인 없이 돌리면 조용히 빈 결과가 적재된다.
    """
    ok, why = available()
    if not ok:
        raise ScheduleUnavailable(why)
    args = ["schtasks", "/Create", "/F", "/IT",
            "/TN", job.task_name,
            "/TR", command_line(job),
            "/SC", job.freq]
    if job.freq == FREQ_HOURLY:
        args += ["/MO", str(max(1, job.interval))]
    else:
        args += ["/ST", job.at]
    log.info("예약 등록: %s", " ".join(args))
    _run(args)
    return " ".join(args)


def unregister(preset: str) -> None:
    ok, why = available()
    if not ok:
        raise ScheduleUnavailable(why)
    _run(["schtasks", "/Delete", "/F", "/TN", f"{PREFIX}{preset}"])


def list_jobs() -> list[str]:
    """등록돼 있는 우리 작업 이름들. 조회 실패는 빈 목록으로 삼킨다."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(["schtasks", "/Query", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("예약 목록 조회 실패: %s", e)
        return []
    names = []
    for line in out.stdout.splitlines():
        cell = line.split(",")[0].strip().strip('"')
        name = Path(cell).name           # 스케줄러는 '\\ETReport_…' 로 적는다
        if name.startswith(PREFIX):
            names.append(name)
    return sorted(set(names))


def _run(args: list[str]) -> None:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise ScheduleUnavailable(f"작업 스케줄러를 실행하지 못했습니다: {e}") from e
    if out.returncode != 0:
        msg = (out.stderr or out.stdout or "").strip()
        raise ScheduleUnavailable(
            f"작업 스케줄러가 거부했습니다 (코드 {out.returncode})\n{msg}\n\n"
            "관리자 권한이 필요하거나 사내 정책이 막고 있을 수 있습니다.")


# ── 헤드리스 실행 ────────────────────────────────────────────
def run_headless(preset_name: str, days: int = 1, log_level: str = "INFO") -> int:
    """예약이 부르는 진입점 — 창을 띄우지 않고 추출·적재만 하고 끝난다.

    반환값이 프로세스 종료 코드다(0=성공). 작업 스케줄러가 이 코드를 이력에
    남기므로, 실패를 0으로 돌려주면 안 된다 — 며칠째 비어 있는 DB를 아무도
    모르게 된다.

    **QApplication을 만들지 않는다.** 파이프라인은 Qt에 의존하지 않고, 창이 없는
    프로세스에서 QApplication을 띄우면 세션에 따라 그 자리에서 멈춘다.
    """
    from datetime import date, timedelta

    from etreport.config.settings import Settings

    preset = None
    settings = Settings.load()
    for p in settings.extract_presets:
        if p.name == preset_name:
            preset = p
            break
    if preset is None:
        names = ", ".join(p.name for p in settings.extract_presets) or "(없음)"
        log.error("추출 프리셋 '%s'을(를) 찾지 못했습니다. 있는 것: %s",
                  preset_name, names)
        return 2
    if not (preset.db_path and preset.reformatter_path):
        log.error("프리셋 '%s'에 DB 또는 리포메터 경로가 없습니다", preset_name)
        return 2

    from etreport.app import _prepare_catalog
    from etreport.data import pipeline

    catalog = _prepare_catalog(log)
    d_to = date.today()
    d_from = d_to - timedelta(days=max(0, days - 1))
    log.info("예약 추출 시작 — 프리셋 '%s' · %s ~ %s", preset_name, d_from, d_to)
    try:
        res = pipeline.run(preset, d_from, d_to, catalog,
                           on_log=lambda m: log.info("  %s", m))
    except Exception as e:
        log.error("예약 추출 실패: %s", e, exc_info=True)
        return 1
    log.info("예약 추출 완료 — %s", res.summary())
    return 0
