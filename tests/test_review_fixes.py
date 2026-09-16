"""코드 리뷰에서 나온 수정들의 회귀 테스트.

각 테스트는 "고치기 전에는 어떻게 잘못됐는지"를 주석으로 남긴다 — 나중에
누가 되돌리면 여기서 걸린다.
"""
from __future__ import annotations

import json
import sys
import zipfile

import polars as pl
import pytest

from etreport.config.settings import Condition
from etreport.data import db, loader
from etreport.data.querybuilder import _q, condition_sql
from etreport.export.excel import sheet_name
from etreport.export.template_writer import merged_frame
from etreport.model import wafers
from etreport.model.specs import PageSpec, PlotSpec, ReportSpec
from etreport.model.split import SplitMatrix
from etreport.paths import cleanup_staging, write_json_atomic
from etreport.render import mpl_renderer
from etreport.update import apply as upd_apply


# ── P0: 업데이트가 소스 트리를 덮어쓰지 못하게 ──────────────
def test_install_dir_refuses_when_not_frozen(monkeypatch):
    """예전에는 저장소 루트를 돌려줘 robocopy /MIR의 대상이 됐다."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    with pytest.raises(upd_apply.UpdateNotApplicable):
        upd_apply.install_dir()


def test_apply_and_restart_refuses_when_not_frozen(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")     # 리눅스 가드보다 먼저 확인
    with pytest.raises(upd_apply.UpdateNotApplicable):
        upd_apply.apply_and_restart(tmp_path)


def test_apply_and_restart_refuses_off_windows(tmp_path):
    with pytest.raises(upd_apply.UpdateNotApplicable):
        upd_apply.apply_and_restart(tmp_path)


def test_update_batch_is_ascii_and_not_mirroring():
    """/MIR는 대상 폴더에서 zip에 없는 파일을 지운다 — 쓰지 않는다."""
    for bat in (upd_apply._BAT_DIR, upd_apply._BAT_EXE):
        bat.encode("ascii")                           # 콘솔 코드페이지 무관
        assert "/MIR" not in bat
        assert "tasklist" in bat                      # 앱이 끝나기를 기다린다
    assert "robocopy" in upd_apply._BAT_DIR and "/E" in upd_apply._BAT_DIR
    # 단일 exe는 파일 하나만 덮어쓴다 — 폴더를 통째로 붓지 않는다
    assert "copy /Y" in upd_apply._BAT_EXE
    assert "robocopy" not in upd_apply._BAT_EXE


def test_extract_rejects_paths_outside_target(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("ETReport/ok.txt", "ok")
        f.writestr("../evil.txt", "no")
    with pytest.raises(ValueError, match="비정상 경로"):
        upd_apply.extract(z)


# ── P1: Figure 누수 ─────────────────────────────────────────
def test_render_does_not_register_figures_in_pyplot():
    """PPT 한 장마다 Figure가 pyplot 전역에 남던 문제.

    덱 하나면 수백 장이라 프로세스가 끝날 때까지 메모리를 붙잡고 있었다.
    """
    import matplotlib.pyplot as plt

    from tests.test_reformatter_apply import rf_of, rule

    rf = rf_of(rule("REAL", "ET_A", "A"), rule("REAL", "ET_B", "B"))
    data = {"": pl.DataFrame({"A": [1.0, 2.0], "B": [3.0, 4.0]})}
    from etreport.model.specs import GroupStyle
    styles = [GroupStyle(gid="", name="전체")]

    before = len(plt.get_fignums())
    for _ in range(30):                               # 예전이면 30개가 쌓인다
        mpl_renderer.render(PlotSpec(x="A", y="B"), data, styles, rf, [], (3, 2))
    assert len(plt.get_fignums()) == before


# ── P1: DuckDB 연결 ─────────────────────────────────────────
def test_store_close_releases_the_file(tmp_path):
    """적재 연결을 닫지 않으면 곧바로 이어지는 읽기 전용 열기가 잠금으로 실패한다."""
    p = tmp_path / "lock.duckdb"
    store = db.Store(p)
    store.con.execute("create table et_data(a int)")
    store.close()
    con = loader.open_readonly(str(p))                # 잠겨 있으면 여기서 터진다
    con.close()


def test_store_works_as_context_manager(tmp_path):
    with db.Store(tmp_path / "ctx.duckdb") as s:
        s.con.execute("create table et_data(a int)")
    loader.open_readonly(str(tmp_path / "ctx.duckdb")).close()


def test_load_state_closes_previous_connection(tmp_path, appdata, monkeypatch):
    """[적용]을 누를 때마다 읽기 전용 연결이 새던 문제."""
    from etreport.data.reformatter import apply as rf_apply
    from etreport.model.state import AppState
    from tests.factory import make_long, make_reformatter

    monkeypatch.setattr(db, "TARGET_CELLS", 10 ** 12)
    rf = make_reformatter(n_real=5, n_addp=1, seed=0)
    src = make_long([r.itemid for r in rf.reals()], lots=1, wafers=2, chips=2)
    pq = tmp_path / "raw_x.parquet"
    rf_apply(rf, src).write_parquet(pq)
    dbp = tmp_path / "a.duckdb"
    with db.Store(dbp) as s:
        db.pivot_and_load(s, [pq])

    st = AppState()
    loader.load_state(st, str(dbp))
    loader.load_state(st, str(dbp))                   # 다시 적용
    assert st.store is None                           # 연결을 쥐고 있지 않는다
    import duckdb
    duckdb.connect(str(dbp)).close()                  # 쓰기 연결도 바로 열린다


# ── P1: 원본 rawdata 보존 ───────────────────────────────────
def test_reformatting_writes_a_separate_file(tmp_path):
    """추출 결과를 덮어쓰면 리포메터를 고쳐 다시 돌릴 때 재추출해야 한다.

    파이프라인은 `data/pipeline.run()`에 있다 — 화면(QThread)과 예약 실행(CLI)이
    같은 코드를 쓰게 하려고 옮겼으므로, 규칙도 그쪽에서 확인한다.
    """
    import inspect

    from etreport.data import pipeline

    src = inspect.getsource(pipeline.run)
    assert "_rf.parquet" in src
    assert "out.write_parquet(f)" not in src


# ── P1: 요약 복사가 화면과 같은 값 ───────────────────────
def test_to_tsv_applies_delta_and_caption():
    from etreport.export.excel import SummaryOptions, to_tsv
    from etreport.render.pptgen import TableData

    td = TableData("DC", [("PA1", ["01", "02"])],
                   [{"cats": ["N", "SVT"], "item": "A",
                     "values": [1.5, -2.5], "offspec": [False, False]}])
    plain = to_tsv(td)
    assert plain.splitlines()[2].split("\t")[3:] == ["1.50", "-2.50"]

    opt = SummaryOptions(delta_vs_ref=True, session_caption="캡션")
    d = to_tsv(td, opt)
    assert d.splitlines()[2].split("\t")[3:] == ["+1.50", "-2.50"]  # Δ 부호
    assert d.strip().endswith("캡션")


# ── P2: 원자적 저장 ────────────────────────────────────────
def test_write_json_atomic_keeps_old_file_on_failure(tmp_path):
    p = tmp_path / "settings.json"
    write_json_atomic(p, {"v": 1})

    class Boom:
        pass

    with pytest.raises(TypeError):                    # 직렬화 불가 → 실패
        write_json_atomic(p, {"v": Boom()})
    assert json.loads(p.read_text(encoding="utf-8")) == {"v": 1}   # 원본 그대로
    assert list(tmp_path.glob("*.tmp*")) == []        # 임시 파일도 안 남는다


def test_settings_save_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from etreport.config.settings import Settings

    s = Settings.defaults()
    s.save()
    again = Settings.load()
    assert [c.name for c in again.analysis_configs] == \
           [c.name for c in s.analysis_configs]


def test_exclusions_save_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from etreport.data import exclusions

    pts: dict[str, dict] = {}
    exclusions.add("/some/db.duckdb", pts, "k1", "이유")
    assert exclusions.load("/some/db.duckdb")["k1"]["reason"] == "이유"


# ── P2: 엑셀 시트 이름 ──────────────────────────────────────
@pytest.mark.parametrize("cat1,expect", [
    ("DC", "DC"),
    ("DC/AC", "DC_AC"),
    ("표[1]", "표_1_"),
    ("", "표"),
    ("가" * 40, "가" * 31),
])
def test_sheet_name_sanitizes(cat1, expect):
    assert sheet_name(cat1, set()) == expect


def test_sheet_name_deduplicates():
    used: set[str] = set()
    a = sheet_name("같은이름" * 8, used)               # 32자 → 잘린다
    b = sheet_name("같은이름" * 8, used)
    assert a != b and len(b) <= 31


# ── P2: 템플릿 되쓰기가 숫자를 문자로 바꾸지 않게 ───────────
def test_merged_frame_keeps_page_and_order_numeric():
    original = pl.DataFrame({
        "_row": [2], "page": [1.0], "x": ["A"], "y": ["B"], "order": [1.0],
        "title1": ["P"], "title2": [""], "Report": ["OTHER"], "Type": ["scatter"],
        "x_name": [""], "y_name": [""],
    })
    spec = ReportSpec(report="R1", pages=[
        PageSpec(number=2, title="P2",
                 slots=[PlotSpec(x="A", y="B")] + [None] * 5)])
    out = merged_frame(original, spec)
    assert out["page"].dtype == pl.Float64
    assert out["order"].dtype == pl.Float64
    assert out["x"].dtype == pl.Utf8


# ── P2: SQL 이스케이프 ──────────────────────────────────────
def test_quote_escapes_backslash_and_quote():
    """값 끝의 역슬래시가 닫는 따옴표를 먹어 SQL이 깨지던 문제."""
    assert _q(r"PA\ ") == r"'PA\\ '"
    assert _q("O'Brien") == "'O''Brien'"
    assert _q(r"a\'b") == r"'a\\''b'"


def test_like_escapes_underscore():
    """`_`는 LIKE에서 한 글자 와일드카드 — 값에 있으면 이스케이프해야 한다."""
    from etreport.config.catalog import Catalog, ColumnInfo

    cat = Catalog()
    cat.columns = [ColumnInfo("root_lot_id", "STRING")]
    sql = condition_sql(Condition("root_lot_id", "PA_1*"), cat)
    assert r"\_" in sql and sql.endswith("%'")


# ── P2: staging 정리 ────────────────────────────────────────
def test_cleanup_staging_removes_only_old_files(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setenv("APPDATA", str(tmp_path))
    from etreport.paths import staging_dir

    d = staging_dir()
    fresh, old = d / "new.parquet", d / "old.parquet"
    fresh.write_bytes(b"x")
    old.write_bytes(b"x")
    past = time.time() - 30 * 86400
    os.utime(old, (past, past))

    assert cleanup_staging(keep_days=7) == 1
    assert fresh.exists() and not old.exists()


# ── P3: split 라벨 왕복 ─────────────────────────────────────
def test_split_labels_survive_separator_in_codes():
    """코드에 ' · '가 들어가면 라벨을 되쪼개던 예전 방식이 어긋났다."""
    df = pl.DataFrame({"lot": ["L1", "L1"], "wafer": ["01", "02"],
                       "M1": ["Base", "A · B"]})
    sm = SplitMatrix.from_dataframe(df)
    combos = sm.combos_for(["M1"])
    assert set(combos) == {("Base",), ("A · B",)}
    styles = sm.styles_for(["M1"])
    assert [s.ref for s in styles] == [True, False]     # Base만 REF
    assert sm.assignment(["M1"])[wafers.key("L1", "02")] == styles[1].gid


# ── P3: split_table 인덱싱 ──────────────────────────────────
def test_split_table_slices_by_position():
    from etreport.render.pptgen import TableData, split_table

    header = [("PA1", [f"{i:02d}" for i in range(1, 15)])]
    rows = [{"cats": ["", ""], "item": "A",
             "values": list(range(14)), "offspec": [False] * 14}]
    parts = split_table(TableData("DC", header, rows), per=12)
    assert len(parts) == 2
    assert parts[0].rows[0]["values"] == list(range(12))
    assert parts[1].rows[0]["values"] == [12, 13]
