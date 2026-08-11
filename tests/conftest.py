"""공통 픽스처.

원칙: **Excel(xlwings)도 bdq도 없이** 전부 돌아야 한다. 리포메터 엑셀을 읽는
경로는 `xlio.read_sheet`를 가짜로 바꿔서 검증한다 — 시트 파싱 규칙과 검증
로직은 그대로 타면서 COM 의존만 걷어낸다.
"""
from __future__ import annotations

import pytest

from tests import factory


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
