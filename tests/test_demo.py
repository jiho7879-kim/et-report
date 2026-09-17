"""데모 한 벌이 **모든 기능을 밟을 수 있는지** 고정한다.

데모는 시연용이자 이 프로젝트의 유일한 통합 점검 수단이다(사내 PC가 아니면
Excel도 bdq도 없다). 그래서 여기서 확인하는 것은 "데모가 뜬다"가 아니라
계약 셋이다.

  ① 데모 데이터가 기능을 덮는가  — seq 분리·retest·음수·NULL·0·CAT4·리포트 2종
  ② 파일(번들) == 화면(in-memory) — DuckDB로 돌아온 값이 화면 값과 같은가
  ③ 가짜 소스가 실제 코드를 태우는가 — 추출·계측·tracking·S3
"""
from __future__ import annotations

import polars as pl
import pytest

from etreport import demo, demo_bundle, demo_data, demo_sources
from etreport.data import loader
from etreport.model.state import AppState


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    """번들은 만드는 데 1초쯤 걸린다 — 모듈에 한 번만 만든다."""
    return demo_bundle.build(tmp_path_factory.mktemp("demo"))


@pytest.fixture
def state() -> AppState:
    st = AppState()
    demo.load_demo(st)
    return st


# ── ① 데모 데이터가 기능을 덮는가 ───────────────────────────
def test_reformatter_is_valid_and_covers_every_function(state):
    rf = state.rf
    assert not rf.errors and not rf.warnings          # 경고 없이 통과해야 한다
    assert len(rf.reals()) == 13 and len(rf.addps()) == 12
    forms = " ".join(r.formula for r in rf.addps()).lower()
    for fn in ("abs(", "sqrt(", "ln(", "log(", "exp(", "min(", "max(",
               "avg(", "sum(", "std("):
        assert fn in forms, f"데모 수식에 {fn}가 없다"
    # ADDP가 ADDP를 참조하는 사슬
    aliases = {r.alias for r in rf.addps()}
    assert any(a in r.formula for r in rf.addps() for a in aliases)


def test_absolute_flags_use_every_token_spelling(state):
    """TRUE·Y·1을 모두 참으로 읽어야 한다(§3.1) — 데모가 그 표기를 섞어 쓴다."""
    spelled = {row[3] for row in demo_data.RULE_ROWS if row[3] not in ("N", "")}
    assert {"TRUE", "Y", "1"} <= spelled
    assert sum(r.absolute for r in state.rf.rules) == 5


def test_frame_has_reserved_columns_and_conditions(state):
    df = state.data
    assert list(df.columns[:7]) == list(loader.RESERVED)
    assert df["lot"].n_unique() == 4
    assert sorted(set(df["step"])) == ["M2ET", "M3ET"]
    assert sorted({int(t) for t in df["temp"]}) == [25, 85, 150]   # 5단위 보정
    assert sorted({int(s) for s in df["site"]}) == [5, 9]


def test_one_lot_has_enough_wafers_for_table_split(state):
    """표 넘침/분할(§7.3)을 눈으로 볼 수 있어야 한다 — 12장 초과 lot이 있다."""
    counts = {lot: len(ws) for lot, ws in state.wafer_columns()}
    assert max(counts.values()) >= 13


def test_data_has_null_zero_and_negative_traps(state):
    df = state.data
    assert df["Rs Poly"].null_count() > 0            # 미측정
    assert df["BVox"].null_count() > 0               # 그 step에서만 측정
    assert df["Idsat/Rs"].null_count() >= df["Rs Poly"].null_count()  # 0 분모
    for col in ("Ioff N SVT", "Idsat P SVT", "Jg Gate"):
        assert df[col].min() > 0                     # ABSOLUTE가 되돌렸다
    raw = demo_data.long_frame()
    assert raw.filter(pl.col("item_id") == "ET_IDSAT_P_SVT")["et_value"].max() < 0


def test_report_covers_pages_trend_table_and_two_reports(state):
    assert state.reports == ["M2_ET", "DEV_EVAL"]
    kinds = {s.type for p in state.report.pages for s in p.slots if s}
    assert {"scatter", "trend"} <= kinds
    modes = {s.mode for p in state.report.pages for s in p.slots if s}
    assert {"site", "avg", "med", "std"} <= modes
    assert len(state.report.table_names()) >= 4      # CAT1이 여러 개
    assert len(state.report.cat_names) >= 3          # CAT2~CAT4 계층


def test_second_report_builds_too(state):
    from etreport.model.templates import build_report
    dev = build_report(state.templates, "DEV_EVAL")
    assert dev.pages and dev.table_rows


def test_split_has_multiple_factors_confound_and_unassigned(state):
    assert state.split.steps == ["M1", "M5", "M8"]
    assert state.split.confounds(["M1"]), "혼입 경고를 보여 줄 조합이 없다"
    assert (state.data["gid"] == "").any(), "미배정 wafer가 하나도 없다"


def test_exclusions_are_seeded_for_the_history_slide(state):
    assert len(state.excluded) == 2
    assert loader.exclusion_frame(state).height == 2
    assert state.active().height == state.data.height - 2


# ── ② 파일(번들) == 화면(in-memory) ─────────────────────────
def test_bundle_writes_every_file(bundle):
    for p in (bundle.db, bundle.rf_csv, bundle.plot_csv, bundle.table_csv,
              bundle.split_csv, bundle.guide):
        assert p.exists() and p.stat().st_size > 0
    assert bundle.tpl_xlsx is None or bundle.tpl_xlsx.exists()


@pytest.fixture
def db_state(bundle, appdata):
    """번들 DB를 읽어 들인 상태. **연결은 반드시 닫는다** — 읽기 전용 연결이
    남아 있으면 (APPDATA가 테스트마다 다르므로) 다음 열기가 설정 충돌로 죽는다.
    """
    st = AppState()
    st.rf = demo_data.reformatter()
    loader.load_state(st, str(bundle.db))
    yield st
    loader.close_store(st)


def test_db_frame_equals_in_memory_frame(db_state, state):
    """DuckDB를 거쳐 온 값이 화면 값과 같아야 한다 — 데모의 핵심 계약."""
    got, want = db_state.data, state.data

    assert set(got.columns) == set(want.columns)
    assert got.height == want.height
    for col in want.columns:
        if col in ("key", "gid"):        # 키 계산 방식이 다르다(내용은 같다)
            continue
        a = sorted((v for v in got[col].to_list() if v is not None), key=str)
        b = sorted((v for v in want[col].to_list() if v is not None), key=str)
        assert len(a) == len(b), col
        if a and isinstance(a[0], float):
            assert all(abs(x - y) < 1e-9 for x, y in zip(a, b)), col
        else:
            assert a == b, col


def test_db_merges_step_seq_into_one_point(db_state):
    """§10.1 — seq 1(DC)과 seq 2(누설)가 한 행에 함께 있어야 한다."""
    st = db_state
    both = st.data.filter(pl.col("Vtlin N SVT").is_not_null()
                          & pl.col("Ioff N SVT").is_not_null())
    assert both.height == st.data.height


def test_db_keeps_only_the_latest_retest(db_state):
    """더 이른 시각의 재측정(값 0.55배)이 살아 있으면 안 된다."""
    w01 = db_state.data.filter((pl.col("lot") == "PA123") & (pl.col("wafer") == "W01")
                         & (pl.col("step") == "M2ET"))
    assert w01.height > 0
    assert w01["Vtlin N SVT"].min() > 0.30      # 0.55배 값이면 0.25 언저리다


def test_apply_config_reads_the_bundle_end_to_end(bundle, appdata):
    """[적용] 경로 그대로 — 리포메터·템플릿·실험 조건·DB를 파일에서 읽는다."""
    from etreport.config.settings import Settings
    from etreport.model.session import apply_config

    st = AppState()
    settings = Settings()
    demo.stage_settings(settings, bundle)
    rep = apply_config(st, settings.analysis_configs[0])
    try:
        _assert_applied(st, rep)
    finally:
        loader.close_store(st)


def _assert_applied(st, rep) -> None:
    assert rep.ok, rep.text()
    assert "REAL 13 · ADDP 12" in rep.text()
    assert st.data.height == demo_data.wide_frame().height
    assert st.reports == ["M2_ET", "DEV_EVAL"]
    assert st.split is not None and st.split.steps == ["M1", "M5", "M8"]


def test_stage_settings_puts_demo_first(bundle):
    from etreport.config.settings import AnalysisConfig, ExtractPreset, Settings

    settings = Settings()
    settings.analysis_configs = [AnalysisConfig(name="내 설정")]
    settings.extract_presets = [ExtractPreset(name="내 프리셋")]
    demo.stage_settings(settings, bundle)

    assert settings.analysis_configs[0].name == demo.DEMO_CONFIG
    assert settings.last_analysis_config == demo.DEMO_CONFIG
    assert [c.name for c in settings.analysis_configs][1:] == ["내 설정"]
    assert settings.extract_presets[0].reformatter_path


def test_template_write_back_round_trips_on_the_bundle(bundle, tmp_path, appdata):
    """[리포트 구성] → [템플릿에 저장] — 데모 번들(csv)에도 되쓸 수 있어야 한다."""
    import shutil

    from etreport.export.template_writer import save_to_template
    from etreport.model.templates import build_report
    from etreport.model.templates import load as tpl_load

    plot = tmp_path / bundle.plot_csv.name
    table = tmp_path / bundle.table_csv.name
    shutil.copy2(bundle.plot_csv, plot)
    shutil.copy2(bundle.table_csv, table)

    rf = demo_data.reformatter()
    t = tpl_load(str(plot), str(table), rf)
    spec = build_report(t, "M2_ET")
    spec.pages[0].slots[0].title = "제목 바꿈"

    bak = save_to_template(t, spec)
    assert bak.endswith(".bak")

    again = build_report(tpl_load(str(plot), str(table), rf), "M2_ET")
    assert again.pages[0].slots[0].title == "제목 바꿈"


# ── ③ 가짜 소스가 실제 코드를 태우는가 ─────────────────────
@pytest.fixture
def sources(tmp_path):
    """가짜 소스는 **반드시 되돌린다** — 남겨 두면 다른 테스트가 오염된다."""
    demo_sources.install(tmp_path / "s3")
    yield tmp_path
    demo_sources.uninstall()


def test_fake_extract_honours_period_and_items(sources):
    from datetime import date

    from etreport.app import _seed_catalog
    from etreport.config.catalog import Catalog
    from etreport.config.settings import Condition
    from etreport.data import extractor

    cat = Catalog()
    _seed_catalog(cat)
    items = [i for i, _a in demo_data.real_items()]
    files = extractor.extract_to_parquet(
        [Condition("line_id", demo_data.LINE_ID, required=True),
         Condition("root_lot_id", "PA123")],
        date(2026, 8, 3), date(2026, 8, 3), cat, sources, item_ids=items)

    got = pl.concat([pl.read_parquet(f) for f in files])
    assert got.height > 0
    assert sorted(set(got["root_lot_id"])) == ["PA123"]
    assert set(got["item_id"]) <= set(items)
    assert sorted({float(t) for t in got["temperature"]}) == [25.0, 85.0]


def test_fake_extract_gives_nothing_outside_the_period(sources):
    from etreport.demo_sources import fake_extract

    sql = ("SELECT * FROM eds.f_et_test WHERE line_id = 'KFBK' "
           "AND tkout_time >= '2020-01-01 00:00:00' "
           "AND tkout_time <  '2020-01-02 00:00:00'")
    assert fake_extract(sql).is_empty()


def test_fake_metrology_follows_subitem_rules(sources, state):
    from etreport.data import metrology as mt

    lots = sorted(set(state.data["lot"]))
    raw = mt.fetch(mt.build_met_sql(lots, item_regex=mt.ITEM_REGEX))
    assert not raw.is_empty()
    assert set(mt.wafer_rows(raw)["subitem_id"]) == {"Q2"}
    assert not set(mt.site_rows(raw)["subitem_id"]) & set(mt.SITE_EXCLUDE)

    data, names = mt.attach(state.data, raw, level="wafer")
    assert names and all("::" in n for n in names)          # step::item
    top = mt.top_factors(data, names, [r.alias for r in state.rf.reals()][:5],
                         state.excluded)
    assert top.height > 0 and top["r"].abs().max() > 0.3


def test_fake_tracking_splits_on_recipe_and_ppid(sources, state):
    from etreport.data import fabtracking as ft

    lots = sorted(set(state.data["lot"]))
    tr = ft.fetch(ft.build_tracking_sql(lots))
    steps = ft.split_steps(tr)
    assert len(steps) == 3, "조건이 갈리는 step만 factor가 되어야 한다"

    # 열 이름은 **step_seq**다(§3) — 데모는 100·300·500을 갈리게 만든다
    assert steps == ["100", "300", "500"]

    sm = ft.to_split_matrix(tr)
    codes = set(sm.wide[steps[0]].to_list()) | set(sm.wide[steps[-1]].to_list())
    assert any(c.startswith("RT_") for c in codes)      # PHOTO = reticle
    assert any(c.startswith("I300_") for c in sm.wide["300"].to_list())  # ppid


def test_fake_s3_lists_uploads_and_downloads(sources, tmp_path):
    from etreport.data import s3

    c = s3.S3Credentials(bucket="demo-bucket", access_key="k", secret_key="s")
    assert "연결됨" in s3.check(c)
    folders, _files = s3.list_folder(c)
    assert "8nm_sram" in folders

    src = tmp_path / "올릴것.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    key = s3.upload(c, src, "17lpv")
    assert key.endswith("올릴것.csv")
    got = s3.download(c, "올릴것.csv", "17lpv", tmp_path / "내려받기")
    assert got.read_text(encoding="utf-8").startswith("a,b")


def test_extract_reformat_load_runs_end_to_end(sources, appdata):
    """[데이터] 화면이 하는 일 그대로 — 추출 → 리포메팅 → 적재 → 읽기.

    가짜인 것은 bdq 응답 하나뿐이다. SQL 조립·청크 분할·스키마 정규화·온도
    보정·리포메팅·피벗 적재·읽기 병합은 전부 사내 PC와 같은 코드가 돈다.
    """

    from etreport.app import _seed_catalog
    from etreport.config.catalog import Catalog
    from etreport.config.settings import Condition
    from etreport.data import extractor
    from etreport.data import reformatter as R
    from etreport.data.db import Store, pivot_and_load

    cat = Catalog()
    _seed_catalog(cat)
    rf = demo_data.reformatter()
    files = extractor.extract_to_parquet(
        [Condition("line_id", demo_data.LINE_ID, required=True)],
        demo_data.D_FROM, demo_data.D_TO, cat, sources,
        item_ids=[i for i, _a in demo_data.real_items()])
    assert len(files) == 3                       # 하루 한 청크

    reformatted = []
    for f in files:
        out = R.apply(rf, pl.read_parquet(f))
        p = f.with_name(f.stem + "_rf.parquet")
        out.write_parquet(p)
        reformatted.append(p)

    db = sources / "추출.duckdb"
    store = Store(db)
    try:
        rows = pivot_and_load(store, reformatted)
    finally:
        store.close()
    assert rows > 0

    st = AppState()
    st.rf = rf
    try:
        loader.load_state(st, str(db))
        assert st.data.height == demo_data.wide_frame().height
        assert sorted({int(t) for t in st.data["temp"]}) == [25, 85, 150]
    finally:
        loader.close_store(st)

    # 같은 파일을 다시 적재해도 늘지 않는다(key_hash ANTI JOIN)
    store = Store(db)
    try:
        assert pivot_and_load(store, reformatted) == 0
    finally:
        store.close()


def test_demo_window_is_staged_for_clicking(bundle, appdata):
    """부팅 뒤 화면 상태 — 데모 설정·프리셋이 골라져 있고 기간이 맞춰져 있다.

    기간이 기본값(최근 7일)으로 남아 있으면 데모에서 [추출]이 0행으로 끝난다.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from etreport.config.catalog import Catalog
    from etreport.config.settings import Settings
    from etreport.ui.mainwindow import MainWindow

    QApplication.instance() or QApplication([])
    st = AppState()
    demo.load_demo(st)
    settings = Settings()
    demo.stage_settings(settings, bundle)

    win = MainWindow(settings, Catalog(), st)
    try:
        demo.stage_window(win)
        assert win.anal_ws.cfg().name == demo.DEMO_CONFIG
        assert win.data_ws.preset().name == demo.DEMO_CONFIG
        assert win.data_ws.d_from.date().toPython() == demo_data.D_FROM
        assert win.data_ws.d_to.date().toPython() == demo_data.D_TO
        assert "[적용]" in win.anal_ws.lbl_apply.text()
    finally:
        win.close()


# ── 화면·PPT까지 실제로 그려지는가 ──────────────────────────
def test_every_demo_slot_renders(state):
    from etreport.render import mpl_renderer

    drawn = 0
    for page in state.report.pages:
        for spec in page.slots:
            if spec is None or spec.type == "table":
                continue
            fig = mpl_renderer.render(spec, {"": state.active()}, state.groups,
                                      state.rf, [], (4, 3))
            assert fig.axes
            drawn += 1
    assert drawn >= 10


def test_demo_deck_builds(state, appdata, tmp_path):
    from pptx import Presentation

    from etreport.export import deckbuild

    out = deckbuild.generate(state, str(tmp_path / "데모.pptx"))
    assert len(Presentation(out).slides) >= len(state.report.pages)
