"""앱 부팅 — 설정·카탈로그 → 상태 → 창 → 업데이트 확인(비동기).

사양서 §13의 부팅 순서를 그대로 따른다. 이 파일이 하는 일은 넷뿐이다.
  ① CLI 파싱 · 로깅 준비
  ② 전역 리소스: 폰트·QSS·설정·컬럼 카탈로그
  ③ 상태를 만들고 창 띄우기
  ④ 종료 시 설정 저장
화면 로직은 전부 ui/ 아래에 둔다 — 여기에 기능을 붙이지 말 것.

배포 형태(PyInstaller --onedir --windowed)에는 콘솔이 없어 `sys.stderr`가
None이고, 예외 트레이스백이 어디에도 남지 않는다. 사내 PC에서 "그냥 아무
반응이 없어요"를 진단할 수단이 필요하므로 파일 로그(`%APPDATA%\\ETReport\\logs`)와
excepthook을 여기서 건다. 앱을 죽이지는 않는다 — 사양서 전반의 오류 처리 방식
그대로 "중단하지 않고 남기고 알린다".
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from etreport import APP_NAME, __version__
from etreport.paths import log_file

LOG_FORMAT = "%(asctime)s  %(levelname)-7s %(name)s  %(message)s"
LOG_MAX_BYTES = 1_000_000        # 회전 1MB × 3개 — 로그가 디스크를 먹지 않게
LOG_BACKUPS = 3
# --log-level DEBUG로 켜도 우리 로그가 남의 라이브러리 수다에 묻히지 않게 한다
# (matplotlib 하나가 부팅 한 번에 수백 줄을 쓴다).
QUIET_LOGGERS = ("matplotlib", "PIL", "fontTools", "urllib3", "asyncio")


# ── ① 로깅 ────────────────────────────────────────────────────
def _setup_logging(level: str) -> None:
    """콘솔(있을 때만) + %APPDATA%\\ETReport\\logs\\etreport.log 회전 파일.

    basicConfig를 쓰지 않는 이유: --windowed exe에서는 sys.stderr가 None이라
    StreamHandler가 첫 로그에서 터진다. 핸들러를 직접 고른다.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):            # 재진입(테스트·재실행) 시 중복 방지
        root.removeHandler(h)

    fmt = logging.Formatter(LOG_FORMAT)
    if sys.stderr is not None:
        con = logging.StreamHandler(sys.stderr)
        con.setFormatter(fmt)
        root.addHandler(con)
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_file(), maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
            encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:                     # 권한·경로 문제 — 콘솔 로그로 계속
        logging.getLogger("etreport").warning("로그 파일을 열지 못했습니다: %s", e)

    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(root.level, logging.WARNING))


def _install_excepthook(log: logging.Logger) -> None:
    """처리되지 않은 예외를 로그에 남기고 한 번만 알린다(앱은 계속 뜬 채로).

    PySide6는 슬롯 안에서 터진 예외를 sys.excepthook으로 넘긴다. 기본 훅은
    stderr에 찍고 마는데, 배포 exe에는 stderr가 없어 아무 일도 일어나지 않은
    것처럼 보인다. 같은 오류가 반복(예: paint 이벤트)될 때 모달이 쌓이지
    않도록 (타입, 메시지)별로 처음 한 번만 창을 띄운다.
    """
    seen: set[tuple[str, str]] = set()
    default = sys.excepthook

    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            default(exc_type, exc, tb)
            return
        log.critical("처리되지 않은 예외", exc_info=(exc_type, exc, tb))
        sig = (exc_type.__name__, str(exc))
        if sig in seen:
            return
        seen.add(sig)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is None:      # 창이 뜨기 전이면 로그로 충분
                return
            QMessageBox.warning(
                None, f"{APP_NAME} — 오류",
                "예기치 않은 오류가 발생했습니다. 이어서 쓸 수 있지만 결과가 "
                "이상하면 앱을 다시 시작하세요.\n\n"
                f"{exc_type.__name__}: {exc}\n\n자세한 내용: {log_file()}")
        except Exception:                    # 알림 실패로 죽지는 않는다
            log.debug("오류 알림 창을 띄우지 못했습니다", exc_info=True)

    sys.excepthook = hook


# ── ② 전역 리소스 ─────────────────────────────────────────────
def _load_style(app) -> None:
    from etreport import fonts

    fonts.setup_qt(app)                       # 한글 폰트 먼저 (OS별로 다르다)
    qss = Path(__file__).with_name("ui") / "style.qss"
    if not qss.exists():                      # 빌드 시 --add-data 누락 등
        logging.getLogger("etreport").warning("style.qss를 찾지 못했습니다: %s", qss)
        return
    ui_font, mono_font = fonts.qss_stacks()
    css = qss.read_text(encoding="utf-8")
    app.setStyleSheet(css.replace("%UI_FONT%", ui_font)
                         .replace("%MONO_FONT%", mono_font))


def _make_qapp(qt_args: list[str]):
    """QApplication 생성 — 인스턴스 만들기 전에 끝내야 하는 설정을 먼저 건다."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    # 125%·150% 배율 Windows에서 정수 반올림 때문에 레이아웃이 튀는 것을 막는다.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    # 우리 플래그는 argparse가 먹었고, 남은 것(-platform offscreen 등)만 Qt에 넘긴다.
    app = QApplication(sys.argv[:1] + qt_args)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("ETReport")       # QStandardPaths·QSettings 기준 이름
    _set_windows_app_id()
    return app


def _set_windows_app_id() -> None:
    """작업표시줄에서 python.exe가 아니라 ET Report로 묶이게 한다(Windows 전용)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDE.ETReport")
    except Exception:                          # 작업표시줄 표시상의 문제일 뿐
        logging.getLogger("etreport").debug("AppUserModelID 설정 생략", exc_info=True)


def _prepare_catalog(log: logging.Logger):
    """컬럼 카탈로그 확보 — 캐시 → bdq 조회 → 기본 목록 순으로 물러선다.

    `ensure()`를 그대로 부르지 않는 이유: 캐시 JSON이 깨져 있으면 캐시 읽기에서
    예외가 나 조회까지 가지 못한다. 깨진 캐시는 버리고 조회로 넘어가야 한다
    (조회에 성공하면 캐시도 새로 쓰인다). bdq가 없는 개발 PC면 기본 목록.
    """
    from etreport.config.catalog import Catalog

    catalog = Catalog()
    try:
        if catalog.load_cache():
            return catalog
    except Exception as e:               # noqa: BLE001 — 캐시는 언제든 버릴 수 있다
        log.warning("카탈로그 캐시가 손상돼 무시합니다(%s)", e)
    try:
        catalog.refresh()                # bdq.getColumnInfo → 캐시 갱신
    except Exception as e:               # noqa: BLE001 — 없어도 앱은 떠야 한다
        log.warning("컬럼 카탈로그 조회 실패(%s) — 기본 컬럼 목록 사용", e)
        _seed_catalog(catalog)
    return catalog


# ── 진입점 ────────────────────────────────────────────────────
def _build_parser():
    import argparse

    ap = argparse.ArgumentParser(
        prog="etreport", description=f"{APP_NAME} — ET 데이터 리포트 자동화")
    ap.add_argument("--demo", action="store_true",
                    help="샘플 데이터로 실행 (UI 개발·시연용, DB·Excel 없이)")
    ap.add_argument("--no-update", action="store_true",
                    help="시작 시 새 버전 확인을 건너뛴다")
    ap.add_argument("--log-level", default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                    help="로그 상세도 (기본 INFO)")
    ap.add_argument("--version", action="version",
                    version=f"{APP_NAME} {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    args, qt_args = _build_parser().parse_known_args(argv)

    _setup_logging(args.log_level)
    log = logging.getLogger("etreport")
    log.info("%s v%s 시작 — Python %s · %s",
             APP_NAME, __version__, sys.version.split()[0], sys.platform)
    _install_excepthook(log)

    from etreport import demo
    from etreport.config.settings import Settings
    from etreport.model.state import AppState
    from etreport.ui.mainwindow import MainWindow

    app = _make_qapp(qt_args)
    _load_style(app)

    settings = Settings.load()                # 깨졌으면 기본값으로 복구(§10.8)
    catalog = _prepare_catalog(log)

    state = AppState()
    if args.demo:
        demo.load_demo(state)
        log.info("데모 모드 — %d 포인트 · item %d",
                 state.data.height, len(state.aliases()))

    win = MainWindow(settings, catalog, state)
    win.show()

    # 업데이트 확인은 창이 뜬 뒤 백그라운드로. 실패는 조용히 무시한다(§11.5).
    if args.no_update or args.demo:
        log.info("업데이트 확인 건너뜀 (%s)", "--no-update" if args.no_update else "데모")
    else:
        try:
            from etreport.update.dialog import check_async
            check_async(win, settings)       # 새 버전 있으면 선택 창
        except Exception as e:               # noqa: BLE001
            log.warning("업데이트 확인 생략: %s", e)

    code = app.exec()
    try:
        settings.save()
    except Exception as e:                   # noqa: BLE001 — 종료는 막지 않는다
        log.error("설정 저장 실패: %s", e)
    log.info("종료 (code=%d)", code)
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
