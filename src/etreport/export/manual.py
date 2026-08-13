"""사용 설명서 PDF 조립 — 데모 화면 캡처를 그대로 넣는다.

Qt의 `QPdfWriter`로 직접 그린다(외부 의존성 없이 한글이 그대로 나온다).
캡처는 `tools/make_manual.py`가 **데모 모드 offscreen**으로 찍어 넘긴다 —
사내 데이터가 들어갈 일이 없다.

만들어진 PDF는 `assets/manual/ET_Report_사용설명서.pdf`로 패키지에 함께
배포되고, [도움말] 메뉴에서 바로 열린다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

#: A4 가로 — 화면 캡처가 가로로 길어 세로보다 잘 맞는다.
PAGE_W_MM, PAGE_H_MM = 297, 210
MARGIN_MM = 14


@dataclass
class Section:
    """설명서 한 절 — 제목 + 본문 + (선택) 캡처 이미지."""
    title: str
    body: list[str] = field(default_factory=list)
    shot: Path | None = None
    caption: str = ""


def manual_path() -> Path:
    """패키지에 함께 배포되는 PDF 경로(있을 수도, 없을 수도 있다)."""
    return (Path(__file__).resolve().parent.parent / "assets" / "manual"
            / "ET_Report_사용설명서.pdf")


def sections(shots: dict[str, Path] | None = None) -> list[Section]:
    """설명서 내용 — 화면 순서(데이터 → 분석 3탭 → 출력)를 그대로 따라간다."""
    s = shots or {}
    return [
        Section(
            "1. 무엇을 하는 도구인가",
            ["ET(전기특성) 계측 데이터를 뽑아 정리하고, 화면에서 살펴본 그대로",
             "요약표와 PPT 리포트를 만들어 주는 도구입니다.",
             "",
             "· [데이터] 탭에서 조건을 걸어 추출·적재하고,",
             "· [분석] 탭에서 그룹을 나눠 보고 표와 그림을 만듭니다.",
             "· 화면에 보이는 숫자와 PPT·엑셀의 숫자는 항상 같습니다.",
             "",
             "준비물은 엑셀 파일 네 가지입니다 — 리포메터, plot 템플릿,",
             "table 템플릿, (선택) 실험 조건. [템플릿] 메뉴에서 예시를",
             "내려받으면 각 파일의 '설명' 시트에 규칙이 정리돼 있습니다."]),
        Section(
            "2. 데이터 추출",
            ["① 프리셋을 고르거나 새로 만듭니다.",
             "② DuckDB 파일과 리포메터를 지정합니다.",
             "③ 기간과 조회 조건을 넣습니다. line_id는 필수입니다",
             "   (파티션 컬럼이라 없으면 전체를 훑어 매우 느립니다).",
             "④ [추출하고 적재]를 누르면 진행 로그가 한 줄씩 쌓입니다.",
             "",
             "조회는 리포메터의 REAL item만 가져오고, item이 많으면",
             "9,999개씩 나눠 기간 × 그룹만큼 병렬로 실행합니다.",
             "SQL 미리보기에는 item 목록 대신 개수만 표시됩니다."],
            s.get("data"), "데이터 탭 — 조건·기간·진행 로그"),
        Section(
            "3. 분석 준비 — [적용]",
            ["왼쪽 도크에서 DB·템플릿·리포메터·실험 조건을 고르고 [적용]을",
             "누릅니다. 파일을 고르는 동안에는 아무것도 읽지 않습니다",
             "— [적용]을 눌러야 한 번에 읽고 검증합니다.",
             "",
             "· 리포트 이름은 템플릿의 Report 열이 정합니다(고르는 콤보가 없습니다).",
             "· 실험 조건이 있으면 factor를 고르고, 혼입 경고를 꼭 확인하세요.",
             "  '어느 step 때문인지 구분할 수 없다'는 경고가 이 도구의 핵심입니다.",
             "· 그룹은 [그룹 편집]에서 손으로 짜거나 [자동 그룹핑]으로 만듭니다.",
             "  DB만 골라 두면 [적용] 전에도 조회·배정할 수 있습니다."]),
        Section(
            "4. 탐색 탭 — 보고 싶은 대로 그리기",
            ["X·Y에 item 이름을 넣고 [그리기]를 누릅니다. 계산은 버튼을",
             "눌렀을 때만 일어납니다(버튼이 주황색이면 다시 그릴 것이 있다는 뜻).",
             "",
             "· 축: 쉼표로 여러 쌍을 넣으면 한 그림에 겹칩니다.",
             "  X에 W 또는 L을 넣으면 기하 trend로 자동 전환됩니다.",
             "· 점: 측정점 그대로 / wafer 평균 · 중앙값 · 산포 중에서 고릅니다.",
             "· 스케일·범위: 자동·log·선형, [범위 직접 지정]을 켜면 지금 보이는",
             "  축 값이 채워집니다.",
             "· 그룹 스타일: 색·심볼·크기·REF를 바로 바꿉니다.",
             "· 점을 클릭하면 제외됩니다(회색 빈 원으로 남고 표·PPT에서도 빠집니다)."],
            s.get("explore"), "탐색 탭 — 산점도와 오른쪽 카드 3종"),
        Section(
            "5. 기하(W/L) trend",
            ["X축에 W 또는 L을 넣으면 리포메터의 WIDTH·LENGTH 값을 X로 삼아",
             "item마다 점을 세우고 그룹별 대표값을 선으로 잇습니다.",
             "",
             "리포메터에 WIDTH·LENGTH 열이 없으면 그 item은 조용히 빠집니다.",
             "plot 템플릿에서는 Type=trend, x=W(또는 L)로 적습니다."],
            s.get("trend"), "탐색 탭 — W trend"),
        Section(
            "6. Summary 탭 — 표",
            ["[표 만들기]를 누르면 CAT1마다 카드 하나가 만들어집니다.",
             "",
             "표의 종류는 콤보에서 고릅니다.",
             "· 평균 — wafer마다 한 열",
             "· 산포 (wafer 내) — wafer 안의 표준편차(n−1)",
             "· 그룹별 평균 — 그룹마다 한 열",
             "· 그룹별 wafer — 열은 wafer이되 그룹으로 묶어 정렬",
             "",
             "규격을 벗어난 셀은 붉게 칠해집니다. [복사]는 화면과 같은 값을",
             "TSV로, [xlsx]는 엑셀로 내보냅니다(사내 PC에서만)."],
            s.get("summary"), "Summary 탭 — 그룹별 평균"),
        Section(
            "7. 리포트 구성 탭 — PPT 만들기",
            ["슬롯을 끌어다 놓아 자리를 바꾸고, 슬롯을 고르면 오른쪽에서",
             "제목·X·Y·점 종류·Y축 스케일을 바꿀 수 있습니다.",
             "[템플릿에 저장]을 누르면 이 배치가 엑셀 템플릿에 되쓰입니다",
             "(.bak 사본을 남깁니다).",
             "",
             "[PPT 생성]이 만드는 순서는 이렇습니다.",
             "① 표지 — LINE_ID·ROOT_LOT_ID·STEP_ID·TEMPERATURE·TKOUT_TIME",
             "② 실험 조건 정리표(실험 조건이 있을 때)",
             "③ plot 페이지 — 실험마다 반복",
             "④ 표 페이지 — CAT1마다 한 장",
             "⑤ 그룹별 평균 표",
             "⑥ 제외 포인트 이력",
             "",
             "슬라이드는 항상 16:9이고, 글자는 9pt 아래로 줄이지 않습니다."],
            s.get("report"), "리포트 구성 탭 — 2×3 슬롯과 인스펙터"),
        Section(
            "8. 알아 두면 좋은 것",
            ["· 제외한 점은 DB에 쓰지 않고 사용자 폴더에 따로 저장돼 다음에도 남습니다.",
             "· 온도는 읽는 시점에 5의 배수 정수로 맞춥니다(23.9 → 25).",
             "· step_seq만 다른 행은 한 측정점으로 합칩니다",
             "  — x와 y가 다른 seq에 있어도 함께 그려집니다.",
             "· ABSOLUTE는 분석할 때도 다시 적용되므로, 이미 음수로 적재된",
             "  DB도 리포메터만 고치면 됩니다.",
             "· [S3 저장소]로 duckdb·csv·sbdf를 사내 스토리지에 올리고 내립니다.",
             "· [inline 계측 불러오기]로 계측값을 붙이면 탐색 X축·표에서 쓰고,",
             "  유의 인자 top-k는 PPT 슬라이드로 나갑니다."]),
    ]


def build_manual(out_path: str | Path, shots: dict[str, Path] | None = None,
                 version: str = "") -> Path:
    """설명서 PDF를 만든다. 캡처가 없으면 글만으로도 만들어진다."""
    from PySide6.QtCore import QMarginsF, QRectF, Qt
    from PySide6.QtGui import QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(out))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageOrientation(QPageLayout.Landscape)
    writer.setPageMargins(QMarginsF(MARGIN_MM, MARGIN_MM, MARGIN_MM, MARGIN_MM))
    writer.setTitle("ET Report 사용 설명서")
    writer.setResolution(150)

    painter = QPainter(writer)
    try:
        page = painter.viewport()
        _cover(painter, page, version)
        for sec in sections(shots):
            writer.newPage()
            _section_page(painter, painter.viewport(), sec, QImage,
                          QFont, QRectF, Qt)
    finally:
        painter.end()
    log.info("사용 설명서 생성: %s", out)
    return out


def _cover(painter, page, version: str) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont

    painter.setPen(QColor("#1d1d1f"))
    f = QFont(painter.font())
    f.setPointSize(34)
    f.setBold(True)
    painter.setFont(f)
    painter.drawText(page.adjusted(0, int(page.height() * 0.30), 0, 0),
                     Qt.AlignHCenter | Qt.AlignTop, "ET Report 사용 설명서")
    f.setPointSize(13)
    f.setBold(False)
    painter.setFont(f)
    painter.setPen(QColor("#6e6e73"))
    sub = "ET 계측 데이터 추출 · 분석 · 리포트 자동화"
    if version:
        sub += f"\n버전 {version}"
    painter.drawText(page.adjusted(0, int(page.height() * 0.44), 0, 0),
                     Qt.AlignHCenter | Qt.AlignTop, sub)


def _section_page(painter, page, sec: Section, QImage, QFont, QRectF, Qt) -> None:
    from PySide6.QtGui import QColor

    f = QFont(painter.font())
    f.setPointSize(17)
    f.setBold(True)
    painter.setFont(f)
    painter.setPen(QColor("#1d1d1f"))
    painter.drawText(page.adjusted(0, 0, 0, 0), Qt.AlignLeft | Qt.AlignTop,
                     sec.title)

    f.setPointSize(10)
    f.setBold(False)
    painter.setFont(f)
    painter.setPen(QColor("#2b2b30"))
    top = int(page.height() * 0.07)
    text_h = int(page.height() * (0.36 if sec.shot else 0.85))
    painter.drawText(QRectF(page.left(), page.top() + top, page.width(), text_h),
                     Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                     "\n".join(sec.body))

    if sec.shot is None or not Path(sec.shot).exists():
        return
    img = QImage(str(sec.shot))
    if img.isNull():
        log.warning("캡처를 읽지 못했습니다: %s", sec.shot)
        return
    area_top = page.top() + top + text_h + int(page.height() * 0.02)
    area_h = page.bottom() - area_top - int(page.height() * 0.05)
    scaled = img.scaled(page.width(), area_h, Qt.KeepAspectRatio,
                        Qt.SmoothTransformation)
    x = page.left() + (page.width() - scaled.width()) // 2
    painter.drawImage(x, area_top, scaled)
    if sec.caption:
        f.setPointSize(8)
        painter.setFont(f)
        painter.setPen(QColor("#6e6e73"))
        painter.drawText(QRectF(page.left(), area_top + scaled.height() + 4,
                                page.width(), 30),
                         Qt.AlignHCenter | Qt.AlignTop, sec.caption)
