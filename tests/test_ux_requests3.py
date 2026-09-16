"""사용자 요청 15건(2026-08-15) 회귀.

번호는 요청서 그대로다.
  1  DuckDB 읽기/쓰기 연결 설정 충돌 안내
  2  S3 트리 — namespace는 키 접두어가 아니다
  3  SQL 조회 OOM — 미리보기는 LIMIT, 저장은 COPY 스트리밍
  4  정규식 조건의 띄어쓰기를 `|`로
  5  fab tracking 자동 조회 결과가 [적용]까지 살아남는다
  9  요약 CAT1 접기
 10  온도 보정을 **적재 시점에** 반영
 11·13  W01·W1·01·1을 같은 wafer로 · 머리글은 있어도 없어도 같다
 15  자동완성이 `_`·`-`·공백을 무시하고 `*`를 받는다

UI가 필요한 것은 offscreen으로 조립만 한다(모달 없음).
"""
from __future__ import annotations

import polars as pl
import pytest

from etreport.data import s3
from etreport.data.extractor import correct_temperature
from etreport.data.querybuilder import regexp_value
from etreport.model import wafers
from etreport.model.split import SplitMatrix
from etreport.ui.widgets.autocomplete import matches, rank, squash
from etreport.ui.widgets.group_dialog import parse_group_rows


# ── 4. 정규식 조건 ───────────────────────────────────────────
def test_regexp_spaces_become_or():
    """★ `P040 L040 P049` → `P040|L040|P049` (RE2에서 공백은 문자 그대로다)."""
    assert regexp_value("P040 L040 P049") == "P040|L040|P049"
    assert regexp_value("  P040   L040  ") == "P040|L040"


def test_regexp_single_token_untouched():
    assert regexp_value("P040") == "P040"
    assert regexp_value("CD|THK") == "CD|THK"


def test_regexp_leaves_real_patterns_alone():
    """공백이 패턴 문법의 일부일 수 있으면 손대지 않는다."""
    assert regexp_value("[A-Z] +[0-9]") == "[A-Z] +[0-9]"
    assert regexp_value(r"P\d+ L\d+") == r"P\d+ L\d+"


def test_regexp_condition_sql_uses_it():
    from etreport.config.catalog import Catalog
    from etreport.config.settings import Condition
    from etreport.data.querybuilder import condition_sql
    sql = condition_sql(Condition("step_id", "P040 L040", mode="regexp"),
                        Catalog())
    assert sql == "step_id REGEXP 'P040|L040'"


# ── 10. 온도 보정은 적재 시점에 ──────────────────────────────
def test_temperature_is_corrected_before_loading():
    """★ 추출 직후 5단위로 맞춘다 — DuckDB에 보정된 값이 저장된다."""
    df = pl.DataFrame({"temperature": [23.9, 24.9, 26.0, 149.0, -38.0, None]})
    got = correct_temperature(df)["temperature"].to_list()
    assert got == [25.0, 25.0, 25.0, 150.0, -40.0, None]


def test_temperature_correction_matches_read_side():
    """★ 적재 보정과 조회 보정(compat.temp_expr)이 **같은 값**을 내야 한다.

    다르면 예전 DB(raw 적재)와 새 DB(보정 적재)가 화면에서 갈린다.
    """
    import duckdb

    from etreport.data.compat import temp_expr
    vals = [23.9, 22.5, 27.5, -2.5, -42.5, 0.0, 149.0, 25.0]
    ours = correct_temperature(pl.DataFrame({"temperature": vals}))["temperature"]
    con = duckdb.connect()
    try:
        theirs = [con.execute(
            f'SELECT {temp_expr("t")} FROM (SELECT ? AS t)', [v]).fetchone()[0]
            for v in vals]
    finally:
        con.close()
    assert [float(x) for x in ours] == [float(x) for x in theirs]


def test_temperature_correction_is_idempotent():
    """이미 보정된 값에 다시 걸어도 같다 — 조회 시점 보정과 겹쳐도 안전."""
    df = pl.DataFrame({"temperature": [23.9, 149.0]})
    once = correct_temperature(df)
    assert correct_temperature(once).equals(once)


def test_normalize_schema_applies_it():
    from etreport.data.extractor import normalize_schema
    df = pl.DataFrame({"temperature": [23.9], "item_id": ["A"],
                       "et_value": [1.0]})
    assert normalize_schema(df)["temperature"][0] == 25.0


# ── 11·13. wafer 표기 통일 ───────────────────────────────────
@pytest.mark.parametrize("raw", ["W01", "W1", "01", "1", " w01 ", "WF-01"])
def test_wafer_forms_are_one_key(raw):
    """★ W01 · W1 · 01 · 1 이 모두 같은 wafer다."""
    assert wafers.norm_wafer(raw) == "1"


def test_unknown_wafer_forms_are_kept():
    """숫자로 안 떨어지는 표기를 숫자로 억지로 바꾸지 않는다."""
    assert wafers.norm_wafer("EDGE") == "EDGE"
    assert wafers.norm_wafer("") == ""
    assert wafers.norm_wafer(None) == ""


def test_wafer_key_expr_matches_scalar():
    """벡터판과 스칼라판이 같은 결정을 해야 한다."""
    vals = ["W01", "W1", "01", "1", "EDGE", "wf_02", None]
    df = pl.DataFrame({"wafer": vals})
    got = df.select(wafers.wafer_key_expr("wafer").alias("k"))["k"].to_list()
    assert got == [wafers.norm_wafer(v) for v in vals]


def test_split_assignment_matches_across_notations():
    """★ 실험 조건표가 `1`이고 DB가 `W01`이어도 그룹이 붙는다."""
    sm = SplitMatrix.from_dataframe(pl.DataFrame(
        {"lot": ["L1", "L1"], "wafer": ["1", "2"], "M1": ["Base", "Hi"]}))
    assign = sm.assignment(["M1"])
    assert assign[wafers.key("L1", "W01")] != assign[wafers.key("L1", "W02")]


def test_manual_groups_match_across_notations():
    """★ 손 배정도 표기를 가리지 않는다."""
    from etreport.data.loader import apply_manual_groups
    df = pl.DataFrame({"lot": ["L1", "L1"], "wafer": ["W01", "W02"],
                       "gid": ["", ""]})
    out = apply_manual_groups(df, {("l1", "1", None, None, None): "g0"})
    assert out["gid"].to_list() == ["g0", ""]


def test_metrology_attaches_across_notations():
    """★ 계측이 `01`, ET가 `W01`이어도 붙는다(예전에는 전부 null)."""
    from etreport.data import metrology as mt
    data = pl.DataFrame({"lot": ["L1"], "wafer": ["W01"], "gid": [""]})
    met = pl.DataFrame({"root_lot_id": ["L1"], "wafer_id": ["01"],
                        "step_id": ["M1"], "item_id": ["CD"],
                        "subitem_id": ["Q2"], "fab_value": [12.5],
                        "line_id": ["KFBK"], "tkout_time": ["2026-08-01"]})
    out, names = mt.attach(data, met, "wafer")
    assert names == ["M1::CD"]
    assert out["M1::CD"][0] == 12.5


# ── 11. 멀티 lot 붙여넣기 — 머리글은 선택 ────────────────────
def test_paste_works_with_and_without_header():
    """★ 머리글을 적든 안 적든 결과가 같다."""
    with_head = "lot\twafer\tgroup\nPA123\tW01\tA\nPA123\t2\tB"
    without = "PA123\tW01\tA\nPA123\t2\tB"
    assert parse_group_rows(with_head) == parse_group_rows(without)
    assert parse_group_rows(without) == [("PA123", "W01", "A"),
                                         ("PA123", "2", "B")]


def test_paste_reads_header_order():
    """머리글이 있으면 열 순서가 달라도 읽는다."""
    text = "group,lot,wafer\nA,PA123,01"
    assert parse_group_rows(text) == [("PA123", "01", "A")]


def test_paste_without_wafer_column():
    assert parse_group_rows("PA123\tA\nPA124\tB") == [
        ("PA123", "", "A"), ("PA124", "", "B")]


# ── 2. S3 트리 ───────────────────────────────────────────────
def test_s3_prefix_ignores_namespace():
    """★ namespace는 키의 일부가 아니다 — 버킷 루트가 트리의 뿌리."""
    c = s3.S3Credentials(namespace="ns1", bucket="b1",
                         access_key="AK", secret_key="SK")
    assert s3._prefix(c, "8nm_sram/2026") == "8nm_sram/2026"
    c.root_prefix = "ns1"                     # 감지됐을 때만 붙는다
    assert s3._prefix(c, "8nm_sram") == "ns1/8nm_sram"


# ── 15. 자동완성 ─────────────────────────────────────────────
def test_autocomplete_ignores_separators():
    """★ `_`·`-`·공백을 무시하고 찾는다."""
    assert squash("Idsat_N-SVT") == squash("Idsat N SVT")
    assert matches("idsatn", "Idsat N SVT")
    assert matches("IDSAT_N", "Idsat N SVT")


def test_autocomplete_star_wildcard():
    """★ `*`는 '사이에 뭐가 있어도 된다'."""
    assert matches("Ids*svt", "Idsat N SVT")
    assert not matches("Ids*xyz", "Idsat N SVT")


def test_autocomplete_ranks_prefix_first():
    items = ["Vtlin N SVT", "Idsat N SVT", "N SVT ratio"]
    assert rank("nsvt", items)[0] == "N SVT ratio"


def test_autocomplete_empty_returns_all():
    items = ["a", "b"]
    assert rank("", items) == items


# ── 1. 연결 충돌 안내 ────────────────────────────────────────
def test_connection_conflict_is_explained():
    """★ 원문만으로는 알 수 없는 오류를 무엇을 해야 하는지로 바꾼다."""
    from etreport.data.loader import explain_conn_error
    msg = explain_conn_error(
        Exception("can't open a connection to same database file with a "
                  "different configuration than existing connections"),
        "/tmp/et.duckdb", write=True)
    assert "et.duckdb" in msg and "분석 화면" in msg


def test_other_errors_pass_through():
    from etreport.data.loader import explain_conn_error
    assert "없는 파일" in explain_conn_error(
        Exception("없는 파일"), "/tmp/x.duckdb", write=False)


def test_readonly_config_is_shared(appdata, tmp_path):
    """★ 읽기 전용 연결은 **같은 설정**이어야 동시에 열린다."""
    import duckdb

    from etreport.data.loader import open_readonly, readonly_config
    p = tmp_path / "x.duckdb"
    duckdb.connect(str(p)).close()
    a = open_readonly(str(p))
    b = open_readonly(str(p))                 # 설정이 같으니 두 번째도 열린다
    try:
        assert readonly_config()["threads"] == 4
    finally:
        a.close()
        b.close()


# ── 3. SQL 조회는 메모리를 쓰지 않는다 ───────────────────────
def _make_db(tmp_path):
    import duckdb
    p = tmp_path / "q.duckdb"
    con = duckdb.connect(str(p))
    con.execute("CREATE TABLE et_data AS SELECT i AS n FROM range(0, 5000) t(i)")
    con.close()
    return p


def test_preview_reads_only_a_page(appdata, tmp_path):
    """★ 미리보기는 LIMIT만 읽고, 전체 행 수용 재실행은 하지 않는다."""
    from etreport.ui.widgets.sql_dialog import preview_query
    head, total = preview_query(str(_make_db(tmp_path)),
                                "SELECT * FROM et_data", rows=10)
    assert head.height == 10 and total is None


def test_duckdb_access_leaves_no_instance_behind(appdata, tmp_path):
    """★ 조회가 끝나면 DuckDB 인스턴스(와 그 캐시)가 남지 않는다.

    분석 화면이 연결(state.store)을 열어 둔 채 SQL 창에서 무거운 조회를 돌리면,
    같은 인스턴스를 공유하는 탓에 조회 캐시가 화면 수명만큼 남아 그 뒤의 모든
    DuckDB 접근이 OOM이 됐다. 이제 읽는 자리는 전부 열고-읽고-닫는다.
    남은 인스턴스가 없다는 증거: **설정이 다른 쓰기 연결이 곧바로 열린다.**
    """
    import duckdb

    from etreport.data import exporting, loader, lotcontext
    from etreport.model.state import AppState
    from etreport.ui.widgets.sql_dialog import preview_query
    p = tmp_path / "big.duckdb"
    con = duckdb.connect(str(p))
    con.execute("CREATE TABLE et_data AS SELECT i::VARCHAR AS lot, "
                "(i % 25)::VARCHAR AS wafer, random() AS a, "
                "md5(i::VARCHAR) AS h FROM range(0, 200000) t(i)")
    con.close()

    def no_instance_left():
        duckdb.connect(str(p)).close()          # 인스턴스가 남아 있으면 실패

    st = AppState()
    loader.load_state(st, str(p))               # [적용]
    assert st.store is None and st.data.height == 200_000
    no_instance_left()
    head, _ = preview_query(str(p), "SELECT h, max(a) FROM et_data GROUP BY h",
                            rows=5)             # SQL 창 — 캐시를 채우는 집계
    assert head.height == 5
    no_instance_left()
    loader.lot_index(str(p))
    loader.wafer_index_from_db(str(p))
    lotcontext.from_db(str(p))
    exporting.copy_to(str(p), "SELECT * FROM et_data LIMIT 10",
                      str(tmp_path / "o.parquet"), "parquet")
    no_instance_left()
    loader.load_state(st, str(p))               # 그 뒤의 조회도 그대로 된다
    assert st.data.height == 200_000
    no_instance_left()


def test_write_connection_has_memory_ceiling(appdata, tmp_path):
    """★ 적재 연결도 메모리 상한·디스크 스필을 쓴다(기본은 물리 메모리 80%)."""
    from etreport.data import db
    with db.Store(tmp_path / "w.duckdb") as s:
        got = s.con.execute(
            "SELECT current_setting('memory_limit'), "
            "current_setting('temp_directory')").fetchone()
    assert got[0].endswith("GiB") and got[1].endswith("duckdb_tmp")


def test_save_streams_through_duckdb(appdata, tmp_path):
    """★ 저장은 DuckDB가 파일로 직접 쓴다 — 결과를 메모리에 올리지 않는다."""
    from etreport.ui.widgets.sql_dialog import copy_to
    db = _make_db(tmp_path)
    out = tmp_path / "out.csv"
    copy_to(str(db), "SELECT * FROM et_data", str(out), "csv")
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")            # 엑셀용 BOM
    assert len(raw.decode("utf-8-sig").strip().splitlines()) == 5001
    assert not (tmp_path / "out.csv.part").exists()   # 임시 파일은 지운다

    pq = tmp_path / "out.parquet"
    copy_to(str(db), "SELECT * FROM et_data", str(pq), "parquet")
    assert pl.read_parquet(pq).height == 5000


def test_save_wide_writes_csv_next_to_db(appdata, tmp_path):
    """★ 적재 후 내보내기는 DB 옆에 et_data.csv를 만든다 (BOM 포함)."""
    from types import SimpleNamespace

    from etreport.data.exporting import save_wide
    db = _make_db(tmp_path)
    msgs = []
    saved = save_wide(SimpleNamespace(
        db_path=str(db), out_dir="", save_csv=True, save_sbdf=False),
        on_log=msgs.append)
    assert saved == [str(tmp_path / "et_data.csv")]
    raw = (tmp_path / "et_data.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert len(raw.decode("utf-8-sig").strip().splitlines()) == 5001
    assert not (tmp_path / "et_data.csv.part").exists()


def test_save_wide_honors_out_dir(appdata, tmp_path):
    """★ out_dir이 있으면 그 폴더로, 없으면 DB 옆으로 간다."""
    from types import SimpleNamespace

    from etreport.data.exporting import save_wide
    db = _make_db(tmp_path)
    out = tmp_path / "out"
    saved = save_wide(SimpleNamespace(
        db_path=str(db), out_dir=str(out), save_csv=True, save_sbdf=False))
    assert saved == [str(out / "et_data.csv")]
    assert (out / "et_data.csv").exists()


def test_save_wide_skips_sbdf_without_library(appdata, tmp_path, monkeypatch):
    """★ SBDF 라이브러리가 없으면 경고만 남기고 건너뛴다 — 실패하지 않는다."""
    from types import SimpleNamespace

    from etreport.data import exporting
    monkeypatch.setattr(exporting, "import_sbdf", lambda: None)
    msgs = []
    saved = exporting.save_wide(SimpleNamespace(
        db_path=str(_make_db(tmp_path)), out_dir="",
        save_csv=False, save_sbdf=True),
        on_log=msgs.append)
    assert saved == []
    assert any("건너뜀" in m for m in msgs)


def test_save_wide_writes_sbdf_via_library(appdata, tmp_path, monkeypatch):
    """★ import_sbdf가 주는 모듈로 export_data를 부른다 (pandas 프레임 경유)."""
    from types import SimpleNamespace

    from etreport.data import exporting

    class FakeSBDF:
        def __init__(self) -> None:
            self.calls = []

        def export_data(self, df, path) -> None:
            self.calls.append((df, path))

    fake = FakeSBDF()
    monkeypatch.setattr(exporting, "import_sbdf", lambda: fake)
    db = _make_db(tmp_path)
    saved = exporting.save_wide(SimpleNamespace(
        db_path=str(db), out_dir="", save_csv=False, save_sbdf=True))
    assert saved == [str(tmp_path / "et_data.sbdf")]
    assert len(fake.calls) == 1
    (df, path) = fake.calls[0]
    assert df.shape == (5000, 1)
    assert path == str(tmp_path / "et_data.sbdf")


def test_save_wide_skips_sbdf_over_row_ceiling(appdata, tmp_path, monkeypatch):
    """★ 행 수 상한을 넘으면 pandas로 올리지 않고 건너뛴다 (헤드리스 OOM 방지)."""
    from types import SimpleNamespace

    from etreport.data import exporting

    class FakeSBDF:
        def __init__(self) -> None:
            self.calls = []

        def export_data(self, df, path) -> None:
            self.calls.append((df, path))

    fake = FakeSBDF()
    monkeypatch.setattr(exporting, "import_sbdf", lambda: fake)
    monkeypatch.setattr(exporting, "SBDF_WARN_ROWS", 100)
    msgs = []
    saved = exporting.save_wide(SimpleNamespace(
        db_path=str(_make_db(tmp_path)), out_dir="",
        save_csv=False, save_sbdf=True),
        on_log=msgs.append)
    assert saved == []
    assert fake.calls == []
    assert any("상한" in m for m in msgs)


# ── 5. fab tracking 결과가 살아남는다 ────────────────────────
def test_tracking_matrix_round_trips_as_text():
    """★ 자동 조회 결과를 붙여넣기 표로 되돌려 저장한다.

    예전에는 matrix만 들고 있다가 창을 닫으면 사라져서 [적용]에 반영되지
    않았다. TSV로 남기면 파일·붙여넣기와 **같은 경로**로 다시 읽힌다.
    """
    from etreport.model.split import parse_split_text
    from etreport.ui.widgets.split_dialog import matrix_to_tsv
    sm = SplitMatrix.from_dataframe(
        pl.DataFrame({"lot": ["L1", "L1"], "wafer": ["01", "02"],
                      "M1": ["PPID_A", "PPID_B"]}), baseline="PPID_A")

    back = parse_split_text(matrix_to_tsv(sm), sm.baseline)

    assert back.steps == sm.steps
    assert back.wide.to_dicts() == sm.wide.to_dicts()
    assert [s.ref for s in back.styles_for(["M1"])] == [True, False]


def test_split_baseline_is_carried_by_config():
    """기준(REF) 코드가 'Base'가 아니어도 [적용]에서 유지된다."""
    from etreport.config.settings import AnalysisConfig
    assert AnalysisConfig(name="x").split_baseline == ""


# ── UI (offscreen 조립만) ────────────────────────────────────
@pytest.fixture
def ws(appdata, monkeypatch):
    """데모 데이터로 분석 워크스페이스 하나.

    모달 창은 headless에서 영원히 기다리므로 가로챈다 — **monkeypatch로**
    걸어야 이 파일이 끝난 뒤 다른 테스트에 남지 않는다.
    """
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    QApplication.instance() or QApplication([])
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.Ok))

    from etreport.config.settings import Settings
    from etreport.demo import load_demo
    from etreport.model.state import AppState, StateBus
    from etreport.ui.analysis_ws import AnalysisWorkspace
    st = AppState()
    load_demo(st)
    w = AnalysisWorkspace(st, StateBus(), Settings())
    yield w
    w.deleteLater()


# ── 9. 요약 CAT1 접기 ─────────────────────────────────────
def test_summary_cat1_folds_and_remembers(ws):
    """★ CAT1마다 토글 · [모두 접기] · 표를 다시 만들어도 접힘이 유지된다."""
    tab = ws.tab_summary
    tab.rebuild()
    assert set(tab._cards) == set(ws.state.report.table_names())

    tab._fold_all(True)
    assert all(c.is_collapsed() for c in tab._cards.values())

    tab.rebuild()                                  # 다시 만들어도 접힌 채로
    assert all(c.is_collapsed() for c in tab._cards.values())

    tab._fold_all(False)
    assert not any(c.is_collapsed() for c in tab._cards.values())


def test_collapsing_hides_the_table_not_the_data(ws):
    """접기는 **보이기만** 바꾼다 — 값은 그대로 있어야 복사·xlsx가 맞는다."""
    tab = ws.tab_summary
    tab.rebuild()
    cat1 = ws.state.report.table_names()[0]
    card = tab._cards[cat1]
    table = card.body.itemAt(0).widget()
    before = table.item(0, 0).text()

    card.set_collapsed(True)
    assert not table.isVisible()
    assert table.item(0, 0).text() == before


# ── 7·8. 모든 plot에 적용 ────────────────────────────────────
def test_point_mode_applies_to_every_slot(ws):
    """★ 점 표시 방식(측정점/wafer 평균…)을 모든 페이지·슬롯에 건다."""
    from etreport.model.specs import POINT_MODES
    tab = ws.tab_report
    tab.rebuild()
    tab._select(0)
    tab.cmb_point.setCurrentIndex(POINT_MODES.index("avg"))

    n = tab._point_to_all()

    slots = [s for p in ws.state.report.pages for s in p.slots
             if s is not None and s.type != "table"]
    assert n == len(slots) > 1
    assert {s.mode for s in slots} == {"avg"}


def test_group_style_applies_to_all_groups(ws):
    """★ 인스펙터 [그룹]의 [스타일을 전 plot에] — 심볼·크기를 한 번에."""
    card = ws.group_section
    card.select(0)
    g0 = ws.state.groups[0]
    g0.symbol, g0.size = "t", 10

    assert card.apply_to_all() == len(ws.state.groups)
    assert {(g.symbol, g.size) for g in ws.state.groups} == {("t", 10)}
    # 색은 그룹을 구분하는 값이라 건드리지 않는다
    assert len({g.color for g in ws.state.groups}) > 1


# ── 14. 빈 슬롯에 plot 만들기 ────────────────────────────────
def test_empty_slot_can_be_filled_from_xy(ws):
    """★ X·Y만 적으면 그 자리에 산점도가 생긴다."""
    tab = ws.tab_report
    ws.state.report.pages[0].slots[5] = None
    tab.rebuild()
    tab._select(5)
    assert tab.ed_sx.isEnabled()                   # 빈 슬롯도 입력칸이 열린다

    x, y = ws.state.aliases()[:2]
    tab.ed_sx.setText(x)
    tab.ed_sy.setText(y)
    assert tab._make_slot()

    spec = ws.state.report.pages[0].slots[5]
    assert (spec.x, spec.y, spec.type) == (x, y, "scatter")
    assert spec.title == f"{y} vs {x}"


def test_geometry_x_makes_a_trend(ws):
    """X가 W·L이면 탐색 탭과 같은 규칙으로 trend가 된다."""
    tab = ws.tab_report
    ws.state.report.pages[0].slots[4] = None
    tab.rebuild()
    tab._select(4)
    tab.ed_sx.setText("W")
    tab.ed_sy.setText(ws.state.aliases()[0])
    tab._make_slot()
    assert ws.state.report.pages[0].slots[4].type == "trend"


def test_make_slot_needs_both_axes(ws):
    tab = ws.tab_report
    ws.state.report.pages[0].slots[3] = None
    tab.rebuild()
    tab._select(3)
    tab.ed_sx.setText("W")
    tab.ed_sy.setText("")
    assert not tab._make_slot()
    assert ws.state.report.pages[0].slots[3] is None


# ── 12. 저장된 붙여넣기 내용으로 창이 뜬다 ───────────────────
def test_split_source_dialog_opens_with_saved_text(appdata):
    """★ 저장된 실험 조건으로 창을 열어도 죽지 않는다.

    예전에는 textChanged를 위젯을 만들기 **전에** 연결해서, 저장된 내용을
    채우는 순간 아직 없는 self.preview를 건드리며 창이 뜨자마자 죽었다.
    """
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    QApplication.instance() or QApplication([])
    from etreport.ui.widgets.split_dialog import SplitSourceDialog

    dlg = SplitSourceDialog(text="lot\twafer\tM1\nPA1\t01\tBase\nPA1\t02\tHi")

    assert dlg.matrix is not None and dlg.matrix.steps == ["M1"]
    assert dlg.preview.rowCount() == 2
    from PySide6.QtWidgets import QDialogButtonBox
    assert dlg.bb.button(QDialogButtonBox.Ok).isEnabled()
    dlg.deleteLater()
