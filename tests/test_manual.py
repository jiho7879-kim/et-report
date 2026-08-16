"""사용 설명서 PDF — 내용·생성·메뉴 연결.

캡처는 데모 모드에서만 찍으므로 사내 데이터가 들어갈 일이 없다.
PDF 조립은 Qt(QPdfWriter)만 쓰므로 리눅스에서도 그대로 검증된다.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def test_sections_cover_every_screen():
    """설명서가 화면 순서를 빠짐없이 훑는다."""
    from etreport.export.manual import sections

    titles = " ".join(s.title for s in sections())
    for word in ("데이터 추출", "적용", "탐색", "trend", "Summary",
                 "리포트 구성"):
        assert word in titles


def test_sections_mention_the_new_features():
    """최신 기능이 설명서에 반영돼 있다(그룹별 표·점 종류·PPT 순서 등)."""
    from etreport.export.manual import sections

    text = "\n".join(
        line for s in sections()
        for line in [s.title, *s.body, *s.tail,
                     *(f"{k} {v}" for k, v in s.rows)])
    for word in ("그룹별 평균", "그룹별 wafer", "중앙값", "표지",
                 "실험 조건", "S3", "inline 계측", "5의 배수",
                 "규격 창", "타깃"):
        assert word in text, word


def test_build_manual_writes_a_pdf(qapp, tmp_path):
    """★ 캡처가 없어도 글만으로 PDF가 만들어진다."""
    from etreport.export.manual import build_manual, sections

    out = build_manual(tmp_path / "m.pdf", version="9.9.9")

    raw = out.read_bytes()
    assert raw[:5] == b"%PDF-"
    pages = raw.count(b"/Type /Page") - raw.count(b"/Type /Pages")
    assert pages == len(sections()) + 1                  # 표지 + 각 절
    assert out.stat().st_size > 5_000


def test_build_manual_embeds_the_screenshots(qapp, tmp_path):
    """캡처를 주면 PDF가 눈에 띄게 커진다(이미지가 들어갔다는 뜻)."""
    from PySide6.QtGui import QColor, QImage

    from etreport.export.manual import build_manual

    shot = tmp_path / "explore.png"
    img = QImage(900, 600, QImage.Format_RGB32)
    img.fill(QColor("#4488cc"))
    img.save(str(shot))

    plain = build_manual(tmp_path / "plain.pdf")
    withimg = build_manual(tmp_path / "shot.pdf", {"explore": shot})

    assert withimg.stat().st_size > plain.stat().st_size


def test_missing_screenshot_is_skipped(qapp, tmp_path):
    """캡처 경로가 잘못돼도 설명서는 만들어진다(중단하지 않는다)."""
    from etreport.export.manual import build_manual

    out = build_manual(tmp_path / "m.pdf", {"explore": tmp_path / "없음.png"})
    assert out.exists()


def test_menu_has_the_manual_entry(qapp, appdata):
    from etreport import demo
    from etreport.config.catalog import Catalog
    from etreport.config.settings import Settings
    from etreport.model.state import AppState
    from etreport.ui.mainwindow import MainWindow

    st = AppState()
    demo.load_demo(st)
    win = MainWindow(Settings.defaults(), Catalog(), st)
    # 메뉴 객체는 파이썬 쪽 참조를 잡아 둬야 한다 — 임시로 꺼내면 GC될 수 있다
    bar_actions = list(win.menuBar().actions())          # 참조를 붙잡는다
    act = next(a for a in bar_actions if a.text() == "도움말")
    help_menu = act.menu()
    labels = [a.text() for a in help_menu.actions()]

    assert labels[0] == "사용 설명서 (PDF)"
    assert "파일 4종 관계도" in labels
    win.close()


def test_bundled_manual_is_shipped():
    """저장소에 만들어 둔 설명서가 패키지 안에 있다(배포본에 함께 들어간다)."""
    from etreport.export.manual import manual_path

    pdf = manual_path()
    assert pdf.exists(), "tools/make_manual.py로 만들어 두세요"
    assert pdf.read_bytes()[:5] == b"%PDF-"
    assert "assets/manual" in pdf.as_posix()
