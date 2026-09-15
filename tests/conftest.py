"""공통 픽스처.

원칙: **Excel(xlwings)도 bdq도 없이** 전부 돌아야 한다. 리포메터 엑셀을 읽는
경로는 `xlio.read_sheet`를 가짜로 바꿔서 검증한다 — 시트 파싱 규칙과 검증
로직은 그대로 타면서 COM 의존만 걷어낸다.
"""
from __future__ import annotations

import pytest

from tests import factory


def qt_until(pred, ms: int = 3000) -> bool:
    """조건이 참이 될 때까지 Qt 이벤트를 돌린다.

    콤보 선택 처리는 팝업을 닫은 뒤 타이머로 실행되므로(ui/tabs/common.on_combo)
    고정 시간 대기는 환경에 따라 흔들린다. 조건으로 기다린다.
    """
    import time

    from PySide6.QtCore import QCoreApplication, QEventLoop

    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        if pred():
            return True
        QCoreApplication.processEvents(QEventLoop.AllEvents, 20)
    return bool(pred())


@pytest.fixture
def no_modal_dialogs(monkeypatch):
    """모달 창은 headless에서 영원히 기다린다 — 전부 눌린 셈 치고 넘긴다.

    **QApplication이 살아 있는지에 따라 결과가 달라지는 코드가 있다**
    (`app._install_excepthook`은 창이 떠 있을 때만 알림을 띄운다). 테스트가
    파일 순서에 따라 앱을 만들어 두면 그런 자리가 조용히 모달로 바뀌므로,
    창을 쓰지 않는 테스트도 이 픽스처를 걸어 둔다.

    돌려주는 리스트에는 (종류, 제목, 본문)이 쌓인다 — 무엇이 떴는지 셀 수 있다.
    """
    from PySide6.QtWidgets import QMessageBox

    seen: list[tuple[str, str, str]] = []

    def record(kind, answer):
        def call(*a, **k):
            args = [x for x in a[1:] if isinstance(x, str)]
            seen.append((kind, *[*args, "", ""][:2]))
            return answer
        return staticmethod(call)

    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, record(name, QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question",
                        record("question", QMessageBox.Yes))
    return seen


@pytest.fixture
def fake_sheet(monkeypatch):
    """`etreport.data.reformatter.load()`가 읽을 시트를 지정한다.

        def test_x(fake_sheet):
            fake_sheet(some_dataframe)
            rf = reformatter.load("아무_경로.xlsx")
    """
    import etreport.data.xlio as xlio

    def install(df, *, expect_path: str | None = None):
        seen: list[tuple] = []

        def _read_sheet(path, sheet=0, force=False):
            seen.append((path, sheet, force))
            if expect_path is not None:
                assert path == expect_path
            return df

        monkeypatch.setattr(xlio, "read_sheet", _read_sheet)
        monkeypatch.setattr(xlio, "read_sheets",
                            lambda path, sheets, force=False:
                            [df for _ in sheets])
        return seen

    return install


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    """%APPDATA%\\ETReport 를 tmp로 돌린다 — 제외 사이드카·xlcache 격리."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path / "ETReport"


@pytest.fixture
def rf_small():
    """REAL 20 · ADDP 6 (ADDP-on-ADDP 포함)."""
    return factory.make_reformatter(n_real=20, n_addp=6, seed=1)


@pytest.fixture
def long_small(rf_small):
    """작은 long 프레임 — REAL 20개 × 키 8개 = 160행."""
    return factory.make_long([r.itemid for r in rf_small.reals()],
                             lots=1, wafers=2, chips=4, seed=1)
