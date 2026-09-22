"""메인 창 — 상단 [데이터 | 분석] 전환 + QStackedWidget."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from etreport import APP_NAME, AUTHOR, __version__
from etreport.config.catalog import Catalog
from etreport.config.settings import Settings
from etreport.model.state import AppState, StateBus
from etreport.paths import appdata_dir
from etreport.ui.analysis_ws import AnalysisWorkspace
from etreport.ui.data_ws import DataWorkspace

log = logging.getLogger(__name__)

HELP_TEXT = """파일 4종은 이렇게 맞물립니다.

  리포메터  (ITEMID → ALIAS · 단위 · 규격 · 계산식)
     ↑ ALIAS로 참조
  plot 템플릿  ─┐
                ├─ Report 열로 짝을 이룹니다
  table 템플릿 ─┘
  실험 조건 (lot·wafer별 step 조건 코드 — 선택)

· 리포메터의 ALIAS가 툴 안의 이름입니다. plot의 x·y와 table의 item_id는
  모두 이 ALIAS를 가리킵니다(ITEMID가 아닙니다).
· plot·table 템플릿은 Report 열 값이 같은 행끼리 한 리포트가 됩니다.
  한 파일에 여러 리포트를 담아 두고 템플릿에서 골라 쓰면 됩니다.
· 실험 조건은 없어도 됩니다 — 그룹은 [그룹 편집]에서 손으로 짤 수 있습니다.
  파일 대신 엑셀에서 복사해 붙여넣어도 됩니다.
· 표는 CAT1마다 한 장씩 자동으로 만들어집니다. plot 템플릿에 표 행을 둘
  필요가 없습니다.

[템플릿] 메뉴에서 4종 예시를 내려받으면 각 파일의 '설명' 시트에 컬럼 의미와
규칙이 정리돼 있습니다."""

SHORTCUT_TEXT = """화면
  Ctrl+1 / Ctrl+2     데이터 · 분석 화면
  F1                  사용 설명서
  F2                  가이드 모드 (다음에 채울 곳을 짚어 준다)

데이터 화면
  F5                  추출하고 적재
  Esc                 진행 중이면 중지

분석 화면
  F5                  적용 (고른 파일을 읽고 검증)
  Ctrl+Enter          보고 있는 탭의 [그리기]·[표 만들기]·[미리보기]
  Ctrl+Z              제외한 점 되돌리기
  F9                  왼쪽 소스 레일 접기·펴기
  F10                 오른쪽 인스펙터 접기·펴기"""


def _copy_out_of_bundle(pdf: Path) -> Path:
    """번들 안의 PDF를 `%APPDATA%`로 복사해 **그 사본을** 열게 한다.

    단일 exe는 실행할 때마다 `%TEMP%\\_MEIxxxxx`에 데이터를 풀고 끝날 때 지운다.
    그런데 PDF 뷰어에 번들 안 경로를 그대로 넘기면 뷰어가 그 파일을 붙잡고 있어
    앱이 끝날 때 폴더를 지우지 못하고 "Failed to remove temporary directory:
    …\\_MEI000032a82"가 뜬다(업데이트 직후 종료에서 실제로 났다). 사본을 열면
    앱을 닫아도 설명서가 살아 있어 읽던 자리를 잃지 않는 덤도 따라온다.

    번들이 아닌 곳(소스 트리)에서 찾은 파일은 그대로 연다.
    """
    from etreport import resources
    b = resources.bundle_dir()
    if b is None or b not in pdf.parents:
        return pdf
    out = appdata_dir() / pdf.name
    try:
        if not out.exists() or out.stat().st_mtime < pdf.stat().st_mtime:
            shutil.copy2(pdf, out)
    except OSError as e:                       # 복사 못 하면 원본이라도 연다
        log.warning("설명서를 %s로 복사하지 못했습니다: %s", out, e)
        return pdf
    return out


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, catalog: Catalog,
                 state: AppState) -> None:
        super().__init__()
        self.settings, self.catalog, self.state = settings, catalog, state
        self.bus = StateBus()
        self.setWindowTitle(f"{APP_NAME}  v{__version__}")
        self.resize(1500, 940)

        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── 상단바 ───────────────────────────────────────────
        bar = QWidget()
        bar.setObjectName("topbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 7, 14, 7)
        h.setSpacing(8)
        logo = QWidget()                      # 강조는 색이므로 색은 QSS가 갖는다
        lg = QHBoxLayout(logo)
        lg.setContentsMargins(0, 0, 0, 0)
        lg.setSpacing(0)
        for text, name in (("ET ", "logo"), ("Report", "logoAccent")):
            lab = QLabel(text)
            lab.setObjectName(name)
            lg.addWidget(lab)
        h.addWidget(logo)
        h.addSpacing(12)
        self.ws_buttons: list[QPushButton] = []
        for i, (name, key) in enumerate((("데이터", "Ctrl+1"), ("분석", "Ctrl+2"))):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setObjectName("wsButton")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"{name} 화면 ({key})")
            b.clicked.connect(lambda _=False, k=i: self._switch(k))
            self.ws_buttons.append(b)
            h.addWidget(b)
        h.addSpacing(6)
        self.menu_host = QHBoxLayout()        # 메뉴는 _build_menus가 채운다
        self.menu_host.setSpacing(2)
        h.addLayout(self.menu_host)
        h.addStretch(1)
        # 가이드 모드 — 다음에 채울 한 곳을 짚어 준다(F2와 같은 동작, 설계 §5)
        self.btn_guide = QPushButton("?")
        self.btn_guide.setObjectName("wsButton")
        self.btn_guide.setCheckable(True)
        self.btn_guide.setCursor(Qt.PointingHandCursor)
        self.btn_guide.setToolTip("다음에 채울 곳을 짚어 줍니다 (F2)")
        self.btn_guide.clicked.connect(self._toggle_guide)
        h.addWidget(self.btn_guide)
        h.addSpacing(6)
        h.addWidget(self._build_rail())
        # 만든 사람 — 크롬 위 보조 글자로 조용히. 문의처가 화면에 있어야 현장에서
        # 버그를 어디로 보낼지 찾지 않는다.
        self.lbl_author = QLabel(AUTHOR)
        self.lbl_author.setObjectName("railMuted")
        self.lbl_author.setToolTip(f"만든 사람 · {AUTHOR}")
        h.addSpacing(10)
        h.addWidget(self.lbl_author)
        v.addWidget(bar)

        # ── 스택 ─────────────────────────────────────────────
        self.stack = QStackedWidget()
        self.data_ws = DataWorkspace(settings, catalog, state, self.bus)
        self.anal_ws = AnalysisWorkspace(state, self.bus, settings)
        self.stack.addWidget(self.data_ws)
        self.stack.addWidget(self.anal_ws)
        v.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.data_ws.loaded.connect(self._after_load)
        for sig in (self.bus.data_changed, self.bus.exclusion_changed,
                    self.bus.status_changed):
            sig.connect(self._refresh_rail)

        self._build_menus()
        self._build_shortcuts()
        self._switch(1)          # 기본: 분석
        self._refresh_rail()

    # ── 상태 레일 ────────────────────────────────────────────
    def _build_rail(self) -> QWidget:
        """지금 무엇이 물려 있고 계산이 최신인지 한 줄로 — 이 앱의 유일한 전역 상태.

        램프(●◐○)를 함께 두는 이유: 색만으로 알리면 색각 이상 사용자에게는
        아무 정보도 아니다.
        """
        rail = QFrame()
        rail.setObjectName("statusRail")
        h = QHBoxLayout(rail)
        h.setContentsMargins(10, 4, 12, 4)
        h.setSpacing(7)
        self.rail_lamp = QLabel("○")
        self.rail_lamp.setObjectName("railLamp")
        self.rail_text = QLabel()
        self.rail_text.setObjectName("railText")
        h.addWidget(self.rail_lamp)
        h.addWidget(self.rail_text)
        return rail

    def _refresh_rail(self) -> None:
        st = self.state
        n = 0 if st.data is None else st.data.height
        n_item = len(st.aliases()) or (
            0 if st.data is None else
            len([c for c in st.data.columns
                 if c not in ("key", "lot", "wafer", "gid")]))
        parts = [st.db_label, f"item {n_item}", f"포인트 {n:,}"]
        if st.excluded:
            parts.append(f"제외 {len(st.excluded)}")
        if st.status_note:
            parts.append(st.status_note)
        self.rail_text.setText("   ·   ".join(parts))

        state = "ok" if st.applied else "dirty" if st.data is not None else "off"
        self.rail_lamp.setText({"ok": "●", "dirty": "◐", "off": "○"}[state])
        self.rail_lamp.setProperty("state", state)
        self.rail_lamp.setToolTip({
            "ok": "설정이 반영돼 있습니다",
            "dirty": "바뀐 설정이 아직 반영되지 않았습니다 — [적용] (F5)",
            "off": "DB가 연결되지 않았습니다",
        }[state])
        self.rail_lamp.style().unpolish(self.rail_lamp)
        self.rail_lamp.style().polish(self.rail_lamp)

    # ── 상단 메뉴 (§11.4) ────────────────────────────────────
    def _build_menus(self) -> None:
        """[템플릿] 예시 내려받기 · [도움말] 네 파일 관계도.

        메뉴는 `menuBar()`에 그대로 만들되 **네이티브 메뉴바는 감추고** 같은
        QMenu를 상단바의 버튼에 건다. 시스템 메뉴 한 줄이 앱 상단바 위에 또
        얹히는 모양을 없애면서, 액션 트리는 한 벌만 유지된다.
        """
        from etreport.export import templates_sample as ts

        m = self.menuBar().addMenu("템플릿")
        for s in ts.all_samples():
            act = m.addAction(f"{s.title} 예시 저장…")
            act.triggered.connect(lambda _=False, k=s.key: self._save_sample(k))
        m.addSeparator()
        m.addAction("4종 한 파일로 저장…").triggered.connect(
            lambda: self._save_sample(None))

        # [도구] — 지금 보는 분석을 **바꾸지 않는** 파일·DB 유틸리티(설계 §2
        # 이동표). 예전에는 도크 맨 아래 접힌 절에 있어 사실상 미발견 기능이었다.
        # 분석 프레임에 컬럼을 붙이는 계측·fab tracking은 여기 두지 않는다 —
        # 그것들은 그릴 수 있는 것 자체를 바꾸므로 소스 레일 [추가 소스]다.
        t = self.menuBar().addMenu("도구")
        t.addAction("S3 저장소…").triggered.connect(
            lambda: self.anal_ws._open_s3())
        t.addAction("SQL 조회 · 내보내기…").triggered.connect(
            lambda: self.anal_ws._open_sql())
        t.addSeparator()
        self.act_cache = t.addAction("Excel 캐시 비우기")
        self.act_cache.setToolTip(
            "Excel 읽기 결과를 로컬에 캐시합니다.\n"
            "누르면 캐시를 비우고 다음에 Excel에서 새로 읽습니다.")
        self.act_cache.triggered.connect(lambda: self.anal_ws._clear_cache())
        t.aboutToShow.connect(self._refresh_cache_action)
        t.addSeparator()
        act_dev = t.addAction("개발자 모드…")
        act_dev.setToolTip("비밀번호를 넣어야 바꿀 수 있는 내부 설정 "
                           "(추출 청크 폭 등)")
        act_dev.triggered.connect(lambda: self.data_ws.open_dev_dialog())

        h = self.menuBar().addMenu("도움말")
        act = h.addAction("사용 설명서 (PDF)")
        act.setShortcut(QKeySequence("F1"))
        act.triggered.connect(self._open_manual)
        h.addAction("파일 4종 관계도").triggered.connect(self._show_help)
        h.addAction("단축키").triggered.connect(self._show_shortcuts)
        h.addAction("버전 · 빌드 정보").triggered.connect(self._show_build_info)

        self.menuBar().setVisible(False)
        for menu in (m, t, h):
            b = QToolButton()
            b.setObjectName("menuButton")
            b.setText(menu.title())
            b.setMenu(menu)
            b.setPopupMode(QToolButton.InstantPopup)
            b.setCursor(Qt.PointingHandCursor)
            self.menu_host.addWidget(b)

    def _toggle_guide(self) -> None:
        """가이드 모드는 분석 화면의 것이다 — 버튼 상태를 그 결과에 맞춘다."""
        on = self.anal_ws.toggle_guide()
        self.btn_guide.setChecked(on)
        if on:
            self._switch(1)

    def _refresh_cache_action(self) -> str:
        """캐시 크기는 **열 때 센다** — 메뉴는 늘 떠 있지 않으니 그때가 가장 싸다."""
        try:
            from etreport.data.xlio import cache_stats
            n, size = cache_stats()
            text = (f"Excel 캐시 비우기   {n}개 · {size / 1024:.0f} KB" if n
                    else "Excel 캐시 비우기   (비어 있음)")
        except Exception:                             # noqa: BLE001
            text = "Excel 캐시 비우기"
        self.act_cache.setText(text)
        return text

    def _build_shortcuts(self) -> None:
        """창 전역 단축키. 화면 안 동작(그리기·적용)은 각 화면이 갖는다."""
        for keys, fn in (
                ("Ctrl+1", lambda: self._switch(0)),
                ("Ctrl+2", lambda: self._switch(1)),
                ("F1", self._open_manual)):
            QShortcut(QKeySequence(keys), self, activated=fn)

    def _show_shortcuts(self) -> None:
        QMessageBox.information(self, "단축키", SHORTCUT_TEXT)

    def _save_sample(self, key: str | None) -> None:
        from etreport.export import templates_sample as ts

        out = QFileDialog.getExistingDirectory(self, "예시를 저장할 폴더")
        if not out:
            return
        try:
            made = (ts.save_all(out) if key is None else
                    [ts.save_sample(next(s for s in ts.all_samples()
                                         if s.key == key), out)])
        except Exception as e:                    # noqa: BLE001 — 안내로 끝낸다
            QMessageBox.warning(self, "예시 저장", f"저장하지 못했습니다: {e}")
            return
        # 성공은 손을 멈추게 하지 않는다 — 알림 한 줄로 끝낸다.
        from etreport.ui.widgets.toast import toast
        note = ("Excel을 쓸 수 없어 CSV로 저장했습니다"
                if made[0].suffix == ".csv" else
                "각 파일의 '설명' 시트에 규칙이 정리돼 있습니다")
        toast(self, f"예시 {len(made)}개를 {out} 에 저장했습니다 — {note}")

    def _open_manual(self) -> None:
        """함께 배포된 사용 설명서 PDF를 기본 뷰어로 연다.

        배포본에 파일이 없을 수도 있으므로(개발 트리에서 아직 안 만든 경우)
        그때는 만드는 방법을 알려 준다 — 앱이 죽지 않는다.
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from etreport.export.manual import manual_path
        pdf = manual_path()
        if not pdf.exists():
            QMessageBox.information(
                self, "사용 설명서",
                "설명서 파일이 없습니다.\n\n"
                "개발 트리에서는 다음으로 만들 수 있습니다:\n"
                "  myenv/bin/python tools/make_manual.py")
            return
        pdf = _copy_out_of_bundle(pdf)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf))):
            QMessageBox.information(self, "사용 설명서", str(pdf))

    def _show_help(self) -> None:
        QMessageBox.information(self, "파일 4종 관계도", HELP_TEXT)

    def _show_build_info(self) -> str:
        """버전 + **이 실행 파일이 언제·어느 소스로 만들어졌는지** + 로그 위치.

        "고친 코드가 exe에 안 들어간 것 같다"는 신고가 반복돼서 넣었다. 빌드 시각과
        커밋 해시가 예전 그대로면 새 빌드를 실행하고 있지 않은 것이고, 바뀌었는데
        동작이 그대로면 그때부터가 진짜 코드 문제다. 두 경우를 구분할 수단이
        없으면 어느 쪽도 고칠 수 없다. 반환값은 테스트가 읽는다.
        """
        from etreport.buildinfo import get as build_info
        from etreport.paths import log_file
        b = build_info()
        text = (f"{APP_NAME}  v{b.version}\n"
                f"만든 사람   {AUTHOR}\n\n"
                f"빌드 시각   {b.built_at or '(소스 실행 — 스탬프 없음)'}\n"
                f"소스 커밋   {b.commit or '(알 수 없음)'}\n"
                f"실행 형태   {b.label().rsplit(' · ', 1)[-1]}\n\n"
                f"로그 파일   {log_file()}")
        QMessageBox.information(self, f"{APP_NAME} — 버전", text)
        return text

    def closeEvent(self, e) -> None:
        """종료 정리 — 실행 중인 추출 스레드와 열려 있는 DB 연결을 닫는다."""
        from etreport.data.loader import close_store

        self.data_ws.shutdown()
        close_store(self.state)
        super().closeEvent(e)

    def _after_load(self) -> None:
        """추출·적재가 끝나면 같은 DB를 분석에 연결하고 화면 전환."""
        path = self.data_ws.preset().db_path
        if path:
            self.anal_ws.connect_db(path)
        self._switch(1)

    def _switch(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.ws_buttons):
            b.setChecked(i == idx)
