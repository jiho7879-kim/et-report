"""사용 설명서 PDF 생성 — 데모 모드 화면을 offscreen으로 찍어 PDF로 묶는다.

    myenv/bin/python tools/make_manual.py

`src/etreport/assets/manual/ET_Report_사용설명서.pdf`에 저장되고, 앱의
[도움말] → [사용 설명서] 메뉴가 이 파일을 연다. 배포 exe에도 함께 들어간다.

데모 데이터만 쓰므로 사내 데이터가 PDF에 섞일 일이 없다.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtWidgets import QApplication

from etreport import __version__, demo
from etreport.app import _load_style
from etreport.config.catalog import Catalog
from etreport.config.settings import Settings
from etreport.export.manual import build_manual, manual_path
from etreport.model.state import AppState
from etreport.ui.mainwindow import MainWindow


def settle(ms: int = 400) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 30)


def capture(win, out_dir: Path) -> dict[str, Path]:
    """설명서에 넣을 화면을 순서대로 찍는다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    shots: dict[str, Path] = {}

    def shot(name: str) -> None:
        settle(600)
        # 레이아웃이 바뀐 뒤에는 offscreen 백킹스토어에 옛 픽셀이 남아 글자가
        # 겹쳐 찍힌다 — 찍기 직전에 한 번 강제로 다시 그린다.
        win.repaint()
        settle(150)
        p = out_dir / f"{name}.png"
        win.grab().save(str(p))
        shots[name] = p
        print(f"  캡처 {p.name}")

    win._switch(0)
    shot("data")

    win._switch(1)
    anal = win.anal_ws
    anal.tabs.setCurrentIndex(0)
    anal.tab_explore.btn_draw.click()
    shot("explore")

    anal.tab_explore.ed_x.setText("W")
    anal.tab_explore.ed_y.setText(", ".join(win.state.aliases()[:2]))
    anal.tab_explore._axes_changed()
    anal.tab_explore.redraw()          # [그리기] — 지연 규약상 버튼으로 그린다
    shot("trend")

    anal.tabs.setCurrentIndex(1)
    anal.tab_summary.agg.setCurrentIndex(2)          # 그룹별 평균
    settle(200)
    anal.tab_summary.btn_build.click()
    shot("summary")

    anal.tabs.setCurrentIndex(2)
    anal.tab_report.btn_draw.click()
    shot("report")
    return shots


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    _load_style(app)

    state = AppState()
    demo.load_demo(state)
    win = MainWindow(Settings.defaults(), Catalog(), state)
    win.resize(1600, 980)
    win.show()
    settle(600)

    shots = capture(win, ROOT / "build" / "manual_shots")
    out = build_manual(manual_path(), shots, version=__version__)
    win.close()
    print(f"완료: {out}  ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
