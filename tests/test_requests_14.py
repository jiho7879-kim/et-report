"""요청 14건 회귀 — 계측 재적용·fab tracking 표·조건 연산자·GEN·적재 보전·
공통 legend·lot 검색·parquet 재사용·업데이트 교체·summary SPEC 열.

각 테스트의 제목 끝 `(§N)`이 요청 번호다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date

import pytest

from tests.test_ux_redesign import qapp, win        # noqa: F401 — 창 조립 픽스처

from etreport.config.catalog import Catalog
from etreport.config.settings import Condition
from etreport.data import querybuilder as qb
from etreport.update import apply as upd_apply
from etreport.update import checker


def _cat() -> Catalog:
    c = Catalog()
    c.columns = []
    return c


# ── §5 data_class ─────────────────────────────────────────────────
def test_where_pins_data_class():
    """일반 측정만 본다 — 추출·미리보기·probe가 모두 같은 조건을 건다. (§5)"""
    conds = [Condition("line_id", "L1", required=True)]
    cat, d = _cat(), date(2026, 8, 4)
    for sql in (qb.build_extract_sql(conds, d, d, cat, ["A"]),
                qb.build_preview_sql(conds, d, d, cat, 3),
                qb.build_item_probe_sql(conds, d, d, cat, 10)):
        assert "data_class = 'GEN'" in sql


# ── §12 업데이트 교체 ──────────────────────────────────────────────
def test_update_batch_waits_without_a_console():
    """`timeout`은 콘솔 없는 배치에서 즉시 실패한다 — 기다리는 척만 했다. (§12)"""
    for bat in (upd_apply._BAT_DIR, upd_apply._BAT_EXE):
        assert "timeout /t" not in bat
        assert "ping -n" in bat                  # 콘솔 없이도 진짜로 쉰다
        assert "goto copy" in bat                # 잠금이 풀릴 때까지 재시도
        bat.encode("ascii")


def test_staged_exe_is_left_beside_the_old_one(tmp_path):
    """교체가 실패해도 손으로 이름만 바꾸면 되도록 exe 옆에 남긴다. (§12)"""
    src = tmp_path / "dl" / "ETReport-9.9.9-win64.exe"
    src.parent.mkdir()
    src.write_bytes(b"new")
    cur = tmp_path / "app" / "ETReport.exe"
    cur.parent.mkdir()
    cur.write_bytes(b"old")

    staged = upd_apply.stage_beside(src, cur)
    assert staged == cur.with_name("ETReport_temp.exe")
    assert staged.read_bytes() == b"new"
    assert cur.read_bytes() == b"old"            # 앱이 도는 동안 현재 exe는 그대로


def test_staging_reports_unwritable_install_dir(tmp_path):
    """권한이 없으면 앱이 살아 있을 때 알린다 — 닫은 뒤 조용히 실패하지 않게. (§12)"""
    src = tmp_path / "new.exe"
    src.write_bytes(b"new")
    cur = tmp_path / "없는폴더" / "ETReport.exe"
    with pytest.raises(upd_apply.UpdateNotApplicable):
        upd_apply.stage_beside(src, cur)


# ── §13 사내 GHE ──────────────────────────────────────────────────
def test_update_checker_points_at_the_internal_repo():
    """사내 GHE·저장소·사설 CA 설정. (§13)"""
    assert checker.API_BASE == "https://github.samsungds.net/api/v3"
    assert (checker.OWNER, checker.REPO) == ("jiho7879-kim", "PA3_SRAM")
    assert checker.TOKEN and checker.CA_BUNDLE is False
    assert "Bearer" in checker._headers()["Authorization"]


# ── §11 추출 원본 재사용 ───────────────────────────────────────────
def test_chunk_path_is_decided_by_the_sql(tmp_path):
    """같은 조회 = 같은 파일 이름. uuid면 재사용할 길이 없었다. (§11)"""
    from etreport.data.extractor import Chunk, Unit, chunk_path

    u = Unit(Chunk(date(2026, 8, 4), date(2026, 8, 4)), 0, 1, ["A"])
    a = chunk_path(tmp_path, u, "SELECT 1")
    assert a == chunk_path(tmp_path, u, "SELECT 1")
    assert a != chunk_path(tmp_path, u, "SELECT 2")
    assert a.name.startswith("raw_20260804_20260804_")


def test_reuse_skips_the_query(tmp_path, monkeypatch):
    """받아 둔 parquet이 있으면 bdq를 부르지 않는다. 꺼져 있으면 부른다. (§11)"""
    import polars as pl

    from etreport.data import extractor as ex

    calls = []

    def fake_fetch(sql):
        calls.append(sql)
        return pl.DataFrame({"line_id": ["L1"], "root_lot_id": ["PA1"],
                             "wafer_id": ["01"], "chip_x_pos": [1],
                             "chip_y_pos": [1], "temperature": [25.0],
                             "step_id": ["M1"], "step_seq": [1],
                             "total_site_cnt": [9],
                             "tkout_time": ["2026-08-04 01:00:00"],
                             "item_id": ["Vt"], "et_value": [0.5]})

    monkeypatch.setattr(ex, "_fetch", fake_fetch)
    conds = [Condition("line_id", "L1", required=True)]
    d = date(2026, 8, 4)
    args = (conds, d, d, _cat(), tmp_path)

    first = ex.extract_to_parquet(*args, item_ids=["Vt"], reuse=True)
    assert len(calls) == 1 and len(first) == 1
    again = ex.extract_to_parquet(*args, item_ids=["Vt"], reuse=True)
    assert len(calls) == 1 and again == first          # 재사용 — 조회 없음
    ex.extract_to_parquet(*args, item_ids=["Vt"], reuse=False)
    assert len(calls) == 2                             # 꺼 두면 늘 다시 받는다


def test_stale_staging_is_not_reused(tmp_path):
    """보관 기간이 지난 파일은 재사용하지 않는다 — cleanup과 같은 기준. (§11)"""
    import os
    import time as _t

    from etreport.data.extractor import is_fresh

    f = tmp_path / "raw.parquet"
    f.write_bytes(b"x")
    assert is_fresh(f)
    os.utime(f, (0, _t.time() - 8 * 86400))
    assert not is_fresh(f)
    assert not is_fresh(tmp_path / "없음.parquet")


# ── §9 lot 검색 ───────────────────────────────────────────────────
def test_lot_search_hides_without_unchecking(qapp, win):
    """검색은 **숨기기만** 한다 — 안 보이는 lot이 조용히 빠지면 안 된다. (§9)"""
    from PySide6.QtCore import Qt

    ws = win.anal_ws
    ws.state.lots_all = ["PA1", "PA2", "PB9"]
    ws.state.lots_selected = []
    ws._fill_lot_list()

    ws.lot_search.setText("pa")
    vis = [ws.lot_list.item(i) for i in range(ws.lot_list.count())
           if not ws.lot_list.item(i).isHidden()]
    assert [x.data(Qt.UserRole) for x in vis] == ["PA1", "PA2"]
    # 숨겼다고 체크가 풀리지 않는다 = SQL이 좁아지지 않는다
    ws._collect_lots()
    assert ws.state.lots_selected == []

    # [해제]는 보이는 것에만 — 찾아 놓고 고르는 흐름
    ws._lot_check_all(False)
    ws._collect_lots()
    assert ws.state.lots_selected == ["PB9"]

    ws.lot_search.setText("")
    assert not any(ws.lot_list.item(i).isHidden()
                   for i in range(ws.lot_list.count()))
