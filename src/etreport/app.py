"""앱 부팅 — 설정·카탈로그 → 상태 → 창 → 업데이트 확인(비동기)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def _load_style(app) -> None:
    from etreport import fonts

    fonts.setup_qt(app)                       # 한글 폰트 먼저 (OS별로 다르다)
    qss = Path(__file__).with_name("ui") / "style.qss"
    if qss.exists():
        ui_font, mono_font = fonts.qss_stacks()
        css = qss.read_text(encoding="utf-8")
        app.setStyleSheet(css.replace("%UI_FONT%", ui_font)
                             .replace("%MONO_FONT%", mono_font))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s")
    log = logging.getLogger("etreport")

    import argparse

    from PySide6.QtWidgets import QApplication

    from etreport import demo
    from etreport.config.catalog import Catalog
    from etreport.config.settings import Settings
    from etreport.model.state import AppState
    from etreport.ui.mainwindow import MainWindow
    ap = argparse.ArgumentParser(prog="etreport")
    ap.add_argument("--demo", action="store_true",
                    help="샘플 데이터로 실행 (UI 개발·시연용)")
    args = ap.parse_args(argv)

    app = QApplication(sys.argv)
    _load_style(app)

    settings = Settings.load()
    catalog = Catalog()
    try:
        catalog.ensure()                 # 최초 1회 bdq.columninfo → 캐시
    except Exception as e:               # noqa: BLE001 — 없어도 앱은 떠야 한다
        log.warning("컬럼 카탈로그 조회 실패(%s) — 기본 목록 사용", e)
        if not catalog.columns:
            _seed_catalog(catalog)

    state = AppState()
    if args.demo:
        demo.load_demo(state)
        log.info("데모 모드 — %d 포인트 · item %d",
                 state.data.height, len(state.aliases()))

    win = MainWindow(settings, catalog, state)
    win.show()

    try:
        from etreport.update.dialog import check_async
        check_async(win, settings)       # 새 버전 있으면 선택 창
    except Exception as e:               # noqa: BLE001
        log.warning("업데이트 확인 생략: %s", e)

    code = app.exec()
    settings.save()
    return code


def _seed_catalog(catalog) -> None:
    """bdq를 못 쓰는 개발 환경용 기본 컬럼 목록."""
    from etreport.config.catalog import ColumnInfo
    catalog.columns = [ColumnInfo(n, t) for n, t in [
        ("line_id", "STRING"), ("tkout_time", "TIMESTAMP"),
        ("root_lot_id", "STRING"), ("lot_id", "STRING"), ("wafer_id", "STRING"),
        ("slot_no", "INT"), ("chip_x_pos", "INT"), ("chip_y_pos", "INT"),
        ("site_no", "INT"), ("total_site_cnt", "INT"), ("temperature", "FLOAT"),
        ("step_id", "STRING"), ("step_seq", "INT"), ("device_id", "STRING"),
        ("product_id", "STRING"), ("mask_set", "STRING"), ("flow_id", "STRING"),
        ("recipe_id", "STRING"), ("eqp_id", "STRING"), ("chamber_id", "STRING"),
        ("lot_type", "STRING"), ("item_id", "STRING"), ("value", "DOUBLE"),
        ("meas_flag", "INT"), ("retest_cnt", "INT"), ("create_dttm", "TIMESTAMP"),
    ]]
    catalog.fetched_at = "(기본값)"


if __name__ == "__main__":
    raise SystemExit(main())
