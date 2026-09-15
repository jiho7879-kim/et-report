"""사용 설명서 PDF 조립 — 데모 화면 캡처를 그대로 넣는다.

Qt의 `QPdfWriter`로 직접 그린다(외부 의존성 없이 한글이 그대로 나온다).
캡처는 `tools/make_manual.py`가 **데모 모드 offscreen**으로 찍어 넘긴다 —
사내 데이터가 들어갈 일이 없다.

색은 화면과 같은 토큰(`ui/theme.TOKENS`)에서 가져온다. 설명서만 옛 팔레트로
남아 화면과 따로 노는 일을 막기 위해서다.

만들어진 PDF는 `assets/manual/ET_Report_사용설명서.pdf`로 패키지에 함께
배포되고, [도움말] → [사용 설명서](F1)가 바로 연다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from etreport.ui.theme import TOKENS

log = logging.getLogger(__name__)

#: A4 가로 — 화면 캡처가 가로로 길어 세로보다 잘 맞는다.
PAGE_W_MM, PAGE_H_MM = 297, 210
MARGIN_MM = 14


@dataclass
class Section:
    """설명서 한 절 — 제목 + 본문 + (선택) 표·캡처.

    `rows`는 '이름 → 설명' 짝이다. 공백으로 열을 맞추면 비례 글꼴에서 어긋나므로
    (예전 단축키 표가 그랬다) 두 열로 **그려서** 맞춘다.
    """
    title: str
    body: list[str] = field(default_factory=list)
    rows: list[tuple[str, str]] = field(default_factory=list)
    tail: list[str] = field(default_factory=list)      # 표 아래 문단
    shot: Path | None = None
    caption: str = ""


def manual_path() -> Path:
    """패키지에 함께 배포되는 PDF 경로(있을 수도, 없을 수도 있다).

    exe에서는 `--add-data`로 실린 자리를 봐야 한다(`etreport/resources`). 만드는 쪽
    (`tools/make_manual.py`)도 이 함수를 쓰므로, 없으면 소스 트리 자리를 돌려준다.
    """
    from etreport import resources
    return resources.path("assets/manual/ET_Report_사용설명서.pdf")


def sections(shots: dict[str, Path] | None = None) -> list[Section]:
    """설명서 내용 — 실제로 일하는 순서(데이터 → 분석 → 출력)를 따라간다."""
    s = shots or {}
    return [
        Section(
            "이 도구가 하는 일",
            ["ET 계측값을 뽑아 정리하고, 화면에서 보던 그대로 표와 PPT로 내보냅니다.",
             "화면의 숫자와 PPT·엑셀의 숫자는 언제나 같습니다 — 그릴 때도 내보낼 때도",
             "같은 계산 코드를 쓰기 때문입니다.",
             "",
             "화면은 둘뿐입니다.",
             "  [데이터]   기간과 조건을 걸어 추출하고 DuckDB에 쌓습니다.",
             "  [분석]     그룹을 나눠 보고, 튀는 점은 클릭해 빼고, 표와 덱을 만듭니다.",
             "",
             "준비물은 엑셀 파일 네 개입니다."],
            rows=[
                ("리포메터", "ITEMID에 부르기 쉬운 이름(ALIAS)을 붙이고 단위·규격·계산식을\n"
                             "정합니다. 툴 안에서는 이 ALIAS로만 부릅니다."),
                ("plot 템플릿", "어떤 그림을 어느 자리에 그릴지."),
                ("table 템플릿", "어떤 item을 어떤 계층으로 표에 넣을지."),
                ("실험 조건", "(선택) lot·wafer별 split 조건. 없으면 그룹을 손으로 짜도 되고,\n"
                              "엑셀에서 복사해 붙여넣어도 됩니다."),
            ],
            tail=["[템플릿] 메뉴에서 예시를 내려받으면 각 파일의 '설명' 시트에 컬럼 의미와",
                  "규칙이 전부 적혀 있습니다. 처음이라면 예시부터 열어 보는 편이 빠릅니다."]),
        Section(
            "화면 읽는 법",
            ["위쪽 검은 띠와 왼쪽 도크가 '도구'이고, 흰 바탕이 '데이터'입니다.",
             "도구 쪽은 일부러 무채색으로 뒀습니다 — 버튼이 알록달록하면 그림 안의",
             "그룹 색·규격선과 눈이 경쟁하니까요. 색은 데이터가 씁니다.",
             "",
             "오른쪽 끝 판독창이 지금 상태를 한 줄로 말해 줍니다."],
            rows=[
                ("● 적용됨", "읽어 둔 설정 그대로입니다."),
                ("◐ 미적용", "바뀐 게 있습니다. [적용](F5)을 누르세요."),
                ("○ 미연결", "아직 DB가 붙지 않았습니다."),
            ],
            tail=["그 옆에 DB 이름 · item 수 · 포인트 수 · 제외한 점 수가 함께 나옵니다.",
                  "",
                  "버튼이 앰버색으로 바뀌고 끝에 점(•)이 붙으면 '눌러야 최신이 된다'는",
                  "뜻입니다. 이 도구는 무엇이든 누를 때만 계산합니다. 조건을 몇 개씩 바꿔",
                  "두고 마지막에 한 번만 그리라는 뜻이기도 합니다."]),
        Section(
            "데이터 추출",
            ["① 설정(프리셋)을 고르거나 [새 설정]으로 하나 만듭니다. DB·리포메터·조건이",
             "   통째로 저장되니, 제품이나 공정마다 하나씩 만들어 두면 편합니다.",
             "② DuckDB 파일과 리포메터를 지정합니다. 없는 파일 이름을 적으면 새로",
             "   만들고, 기존 파일을 고르면 뒤에 이어서 쌓습니다(같은 포인트는 안 겹칩니다).",
             "③ 기간과 조회 조건을 넣습니다. line_id는 반드시 넣으세요 — 파티션 컬럼이라",
             "   빠지면 전체를 훑느라 훨씬 오래 걸립니다.",
             "④ [추출하고 적재](F5). 진행 로그가 한 줄씩 쌓이고, 멈추려면 Esc입니다.",
             "",
             "조회는 리포메터의 REAL item만 가져옵니다. item이 많으면 9,999개씩 나눠",
             "기간 × 그룹만큼 병렬로 돌립니다. 그래서 SQL 미리보기에는 item 목록 대신",
             "개수만 적혀 있습니다(24,000개를 문자열로 펴면 미리보기가 43만 자가 됩니다).",
             "",
             "온도는 적재할 때 5의 배수 정수로 맞춥니다(23.9 → 25). 같은 조건인데",
             "소수점 때문에 wafer가 갈라지는 일을 막기 위해서입니다."],
            shot=s.get("data"),
            caption="데이터 화면 — 왼쪽에 대상·기간, 오른쪽에 조건과 SQL, 아래에 진행 로그"),
        Section(
            "분석 준비 — [적용] 한 번",
            ["왼쪽 도크에서 DB·템플릿·리포메터·실험 조건을 고르고 [적용](F5)을 누릅니다.",
             "파일을 고르는 동안에는 아무것도 읽지 않습니다. Excel을 열었다 닫는 데만",
             "1~3초가 걸려서, 고를 때마다 읽으면 준비가 한없이 느려지기 때문입니다.",
             "[적용]을 누른 그 순간 한 번에 읽고 검증하고, 결과를 한 화면에 모아 줍니다.",
             "",
             "· 리포트 이름은 템플릿의 Report 열이 정합니다. 고르는 콤보는 없습니다.",
             "· 실험 조건을 붙였다면 factor를 고르고 혼입 경고를 꼭 보세요.",
             "  '이 그룹 안에 다른 조건이 섞여 있다'는 경고가 이 도구의 핵심입니다.",
             "  섞인 채로 비교하면 어느 step 때문에 갈렸는지 영원히 알 수 없습니다.",
             "· 그룹은 [그룹 편집]에서 손으로 짜거나 자동 그룹핑으로 만듭니다.",
             "  DB만 골라 두면 [적용] 전에도 조회하고 배정할 수 있고, 그렇게 짜 둔",
             "  그룹은 [적용] 뒤에도 그대로 남습니다.",
             "· 자주 안 쓰는 것(S3·inline 계측·SQL·Excel 캐시)은 [도구]를 펼치면 나옵니다."]),
        Section(
            "탐색 — 보고 싶은 대로 그리기",
            ["X·Y에 item 이름을 넣고 [그리기](Ctrl+Enter). 축 칸은 자동완성이 됩니다.",
             "",
             "· 종류   산점도 · boxplot · 기하 trend 중에 고릅니다. 종류를 바꾸면",
             "         X가 뜻하는 것이 달라지므로(item ↔ 나눌 기준 ↔ W·L) 자동완성",
             "         후보와 X 값도 함께 맞춰 줍니다.",
             "· 축     쉼표로 여러 쌍을 넣으면 한 그림에 겹쳐 그립니다.",
             "         X에 W나 L을 넣으면 기하 trend로 알아서 바뀝니다.",
             "· 점     측정점 그대로 / wafer 평균 · 중앙값 · 산포(σ) 중에 고릅니다.",
             "· 범위   기본은 자동입니다(규격 ∪ 데이터를 중심 기준 ×1.2).",
             "         로그 축이면 여백도 로그로 줍니다 — 데이터가 있는 만큼만 열려서",
             "         빈 자릿수가 화면을 잡아먹지 않습니다.",
             "         [범위 직접 지정]을 켜면 지금 보이는 축 값이 칸에 채워집니다 —",
             "         빈칸부터 시작하면 자릿수를 손으로 옮겨 적어야 하니까요.",
             "· 스타일 그룹마다 색·심볼·크기를 바꾸고 REF를 지정합니다.",
             "",
             "빨간 사각형이 규격 창(SPECLOW·SPECHIGH), 파란 X가 타깃입니다. 한쪽 규격이",
             "없으면 그 변은 축 끝까지 열어 두고, 쉼표로 쌍을 여럿 넣었으면 창과 타깃도",
             "쌍마다 하나씩 그립니다. 색으로 쌍을 구분하지는 않습니다 — 점 색은 그룹의",
             "몫이니까요.",
             "",
             "튀는 점은 클릭해서 바로 빼세요. 회색 빈 원으로 남고 표·PPT에서도 빠집니다.",
             "잘못 뺐으면 Ctrl+Z. 제외 목록은 DB가 아니라 사용자 폴더에 DB 경로별로",
             "저장되므로, 앱을 껐다 켜도 그대로 남아 있습니다."],
            shot=s.get("explore"),
            caption="탐색 — 산점도, 오른쪽에 축·범위·그룹 스타일"),
        Section(
            "기하(W/L) trend",
            ["X축에 W 또는 L을 넣으면 리포메터의 WIDTH·LENGTH 값을 X로 삼아 item마다",
             "점을 세우고, 그룹별 대표값을 선으로 잇습니다. 소자 크기에 따라 특성이",
             "어떻게 움직이는지 한 장으로 보는 그림입니다.",
             "",
             "리포메터에 WIDTH·LENGTH가 비어 있는 item은 조용히 빠집니다(그리다 멈추지",
             "않습니다). plot 템플릿에 넣을 때는 Type=trend, x=W(또는 L)로 적으세요.",
             "대표값은 오른쪽 [점] 콤보에서 평균·중앙값·산포 중에 고를 수 있습니다."],
            shot=s.get("trend"), caption="탐색 — W trend"),
        Section(
            "boxplot — 나눠서 분포 보기",
            ["종류를 boxplot으로 바꾸면 X가 item이 아니라 '나눌 기준'이 됩니다.",
             "Y에 적은 item의 분포가 기준 값마다 상자 하나로 그려집니다.",
             "",
             "X에 넣을 수 있는 것들입니다."],
            rows=[
                ("lot+wafer", "lot과 wafer를 합친 이름 — wafer별 분포를 한눈에"),
                ("lot / wafer", "한쪽만 기준으로"),
                ("gid", "실험 조건 그룹 (축에는 그룹 이름으로 적힙니다)"),
                ("step / temp / site", "측정 조건"),
                ("(직접 뽑은 컬럼)", "[fab tracking 불러오기]에서 이름을 정해 붙인 것"),
            ],
            tail=["그룹이 여럿이면 한 자리에 상자를 나란히 놓고 그룹 색을 칠합니다.",
                  "수염은 1.5×IQR(표준 Tukey)이고, 규격선과 타깃은 가로선으로 긋습니다.",
                  "plot 템플릿에 넣을 때는 Type=box, x에 기준 이름을 적으세요.",
                  "",
                  "숫자 item은 X 후보에 넣지 않습니다 — 값마다 상자가 하나씩 생겨",
                  "그림이 뜻을 잃기 때문입니다. 그 값으로 나누고 싶다면 fab tracking에서",
                  "조건 컬럼으로 뽑아 오는 것이 맞는 길입니다."]),
        Section(
            "요약 — 표",
            ["[표 만들기](Ctrl+Enter)를 누르면 CAT1마다 카드가 하나씩 만들어집니다.",
             "표가 화면을 다 먹으면 카드 제목 왼쪽 ▾로 접어 두고 필요한 것만 펴세요.",
             "접기는 다시 계산하지 않고 보이기만 바꿉니다.",
             "",
             "표 종류는 콤보에서 고릅니다."],
            rows=[
                ("평균", "wafer마다 한 열"),
                ("산포 (wafer 내)", "wafer 안의 표준편차(n−1)"),
                ("그룹별 평균", "그룹마다 한 열"),
                ("그룹별 wafer", "열은 wafer이되 그룹으로 묶어 정렬"),
            ],
            tail=["규격(SPECLOW·SPECHIGH)을 벗어난 셀은 붉게 나옵니다.",
                  "[복사]는 화면과 똑같은 값을 TSV로 클립보드에 넣어 주니 메일이나 슬랙에",
                  "그대로 붙이면 됩니다. [xlsx]는 엑셀 파일로 저장합니다(사내 PC 전용)."],
            shot=s.get("summary"), caption="요약 — 그룹별 평균 표"),
        Section(
            "리포트 구성 — PPT 만들기",
            ["슬롯을 끌어다 놓아 자리를 바꾸고, 슬롯을 고르면 오른쪽에서 제목·X·Y·",
             "점 종류·Y축 스케일을 바꿉니다. [템플릿에 저장]을 누르면 이 배치가 엑셀",
             "템플릿에 되쓰입니다(.bak 사본을 남기니 안심하고 눌러도 됩니다).",
             "",
             "[PPT 생성]이 만드는 순서입니다.",
             "  ① 표지 — LINE_ID · ROOT_LOT_ID · STEP_ID · TEMPERATURE · TKOUT_TIME",
             "  ② 실험 조건 정리표 (실험 조건을 붙였을 때)",
             "  ②' fab tracking 조건표 (컬럼을 뽑아 붙였을 때)",
             "  ③ plot 페이지 — 실험마다 반복",
             "  ④ 표 페이지 — CAT1마다 한 장",
             "  ⑤ 그룹별 평균 표",
             "  ⑥ 유의 인자 top-k (inline 계측을 분석했을 때)",
             "  ⑦ 제외·필터로 빠진 점 이력",
             "",
             "표는 실험과 상관없이 한 벌만 만듭니다. 슬라이드는 항상 16:9이고, wafer가",
             "많아도 글자를 9pt 아래로 줄이지 않습니다 — 대신 표를 넘치게 두거나",
             "12장씩 나눠 담습니다(오른쪽 [표 슬라이드]에서 고릅니다)."],
            shot=s.get("report"),
            caption="리포트 구성 — 2×3 슬롯과 오른쪽 인스펙터"),
        Section(
            "이상치 필터 — 그리기 전에 걸러 내기",
            ["도크 [이상치 필터]를 켜면 표와 그림을 만들기 전에 사분위수 밖의 점을",
             "빼 줍니다. 상자그림의 수염과 같은 계산이지만 배수가 다릅니다.",
             "",
             "  아래 한계 = Q1 − k × IQR       위 한계 = Q3 + k × IQR",
             "",
             "k는 직접 정합니다. 3.0과 4.5를 프리셋으로 뒀고 아무 값이나 넣어도 됩니다.",
             "상자그림 수염(1.5)보다 크게 잡는 이유는, 여기서 하는 일이 '눈에 띄게",
             "하기'가 아니라 '버리기'이기 때문입니다. 1.5로 거르면 정상 산포의 꼬리까지",
             "잘려 나갑니다.",
             "",
             "사분위수는 step·온도마다 따로 구합니다. 25 ℃와 125 ℃를 한데 섞어 세면",
             "멀쩡한 고온 측정이 통째로 이상치가 되기 때문입니다. [item 전체]로 바꿀",
             "수도 있습니다. 점이 12개도 안 되는 묶음은 아예 건드리지 않습니다 —",
             "그런 데서 IQR을 구하면 정상값이 이상치가 됩니다.",
             "",
             "걸러진 점은 버리지 않습니다. 그림에는 회색 빈 원으로 그대로 남고,",
             "[걸러진 점 보기]에 무엇이 어느 item 때문에 빠졌는지 이유가 적힙니다.",
             "PPT의 마지막 이력 장에도 함께 실립니다. 손으로 뺀 점과는 따로 기록되니",
             "필터를 껐다 켜도 손으로 뺀 것은 그대로 남습니다.",
             "",
             "설정만 바꾸면 아직 반영되지 않습니다 — [적용](F5)을 눌러야 다시 겁니다.",
             "데이터를 버리는 일이라 더더욱 누를 때만 돕니다."]),
        Section(
            "fab tracking — 공정 조건 가져오기",
            ["[도구] → [fab tracking 불러오기]는 lot·wafer가 어느 step에서 어떤 조건을",
             "받았는지 가져옵니다. 조회 조건은 지금 분석 중인 DB에서 읽어 채워 둡니다."],
            rows=[
                ("line · process · part", "분석 중인 lot의 값 그대로"),
                ("기간", "ET tkout_time 기준 180일 이전부터 tkout_time까지"),
                ("SQL", "위 조건으로 만든 문장. 직접 고쳐도 됩니다"),
            ],
            tail=["기간을 이렇게 잡는 이유는 계측·tracking이 ET보다 앞선 공정에서",
                  "찍히기 때문입니다. ET 이후를 볼 이유가 없고, 기간이 없으면 예전에",
                  "같은 이름으로 돌았던 lot이 섞여 들어옵니다.",
                  "",
                  "[가져올 컬럼] 표에서 뽑을 것을 줄마다 정합니다. 이름은 자유롭게",
                  "쓰세요 — 그 이름이 그대로 boxplot의 X, 표의 범주, PPT 표의 머리글이",
                  "됩니다. 원본 컬럼은 자동(PHOTO는 recipe·그 외는 ppid) 외에",
                  "ppid · reticle_id · eqp_id · chamber_id · step_seq 같은 것을 고를 수",
                  "있고, step을 지정하면 그 step의 값만 가져옵니다. 같은 step에서",
                  "recipe와 설비를 각각 다른 열로 뽑아도 됩니다.",
                  "",
                  "[적용]을 누르면 (lot, wafer)로 붙습니다. 조건을 고쳐 다시 뽑으면",
                  "같은 이름의 열은 새 값으로 바뀝니다.",
                  "",
                  "inline 계측([도구] → [inline 계측 불러오기])도 같은 방식으로 기간과",
                  "SQL을 손볼 수 있습니다."]),
        Section(
            "예약 실행 — 앱을 켜 두지 않아도",
            ["[데이터] 화면의 [예약 실행…]으로 프리셋 하나를 정해진 시각에 자동으로",
             "돌릴 수 있습니다. 앱이 꺼져 있어도 Windows 작업 스케줄러가 실행합니다.",
             "",
             "주기(매일·매주·몇 시간마다)와 시각, 그리고 '최근 며칠을 추출할지'를",
             "정하면 됩니다. 같은 포인트는 다시 넣지 않으니 기간을 2~3일로 넉넉히",
             "잡아 두면 하루 걸러 실패해도 다음 실행이 메워 줍니다.",
             "",
             "다만 조건이 있습니다. PC가 켜져 있어야 하고, 그 계정으로 로그인되어",
             "있어야 합니다 — 사내망 조회 권한과 Excel이 계정에 묶여 있어서,",
             "로그인 없이 돌리면 조용히 빈 결과가 쌓입니다. 예약은 저장된 프리셋으로",
             "돌기 때문에 화면에서 고친 조건은 저장해 두어야 반영됩니다.",
             "",
             "실행 이력은 Windows 작업 스케줄러(taskschd.msc)에서, 자세한 로그는",
             "%APPDATA%\\ETReport\\logs\\etreport.log에서 볼 수 있습니다."]),
        Section(
            "단축키",
            ["손이 마우스로 가지 않게, 자주 하는 것만 키에 걸어 뒀습니다."],
            rows=[
                ("Ctrl+1 / Ctrl+2", "데이터 · 분석 화면 전환"),
                ("F5", "데이터 화면에서는 [추출하고 적재], 분석 화면에서는 [적용]"),
                ("Ctrl+Enter", "보고 있는 탭의 주 동작\n"
                               "([그리기] · [표 만들기] · [미리보기])"),
                ("Ctrl+Z", "방금 뺀 점 되돌리기"),
                ("Esc", "진행 중인 추출 중지"),
                ("F1", "이 설명서 열기"),
            ],
            tail=["탭 이동은 Tab·Shift+Tab, 누르는 건 Space나 Enter입니다. 포커스가 있는",
                  "곳에는 청록 테두리가 생기니 지금 어디에 있는지 눈으로 따라갈 수 있습니다.",
                  "",
                  "같은 목록은 [도움말] → [단축키]에도 있습니다."]),
        Section(
            "알아 두면 좋은 것",
            ["· 제외한 점은 DB를 건드리지 않습니다. 사용자 폴더에 DB별로 따로 저장되니",
             "  같은 DB를 다시 열면 그대로 살아 있습니다.",
             "· step_seq만 다른 행은 한 측정점으로 합쳐 읽습니다. x가 seq 1에, y가 seq 2에",
             "  기록돼 있어도 한 점으로 그려집니다(step_id·온도·site 수가 다르면 별개).",
             "· ABSOLUTE는 분석할 때 다시 겁니다. 이미 음수로 적재된 DB라도 리포메터만",
             "  고치면 되고, 다시 추출할 필요가 없습니다. 스케일은 다시 걸지 않습니다",
             "  — 두 번 곱해지면 값이 틀어지니까요.",
             "· Excel은 한 번 읽고 캐시합니다. 원본을 고치면 자동으로 다시 읽지만,",
             "  이상하다 싶으면 [도구] → [Excel 캐시]를 눌러 비우세요.",
             "· 추출한 원본 parquet은 일주일 보관합니다. 리포메터만 고쳐 다시 돌릴 때",
             "  재추출 없이 그 파일을 씁니다.",
             "· 뭔가 이상하면 [도움말] → [버전]과 로그 파일을 함께 알려 주세요.",
             "  로그는 %APPDATA%\\ETReport\\logs\\etreport.log 에 쌓입니다."]),
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
        secs = sections(shots)
        _cover(painter, painter.viewport(), version, len(secs))
        for i, sec in enumerate(secs, 1):
            writer.newPage()
            _section_page(painter, painter.viewport(), sec, i, len(secs),
                          version, QImage, QFont, QRectF, Qt)
    finally:
        painter.end()
    log.info("사용 설명서 생성: %s", out)
    return out


def _cover(painter, page, version: str, n_sections: int) -> None:
    """표지 — 왼쪽 그래파이트 판에 제목, 오른쪽은 비워 둔다(계측기 패널)."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont

    # 여백 바깥까지 칠한다 — page는 인쇄 여백 안쪽이라, 그대로 칠하면 판이
    # 종이 한가운데 떠 있는 것처럼 보인다(표지는 가장자리까지 가야 판이다).
    bleed = page.width() * 0.06
    band = QRectF(page.left() - bleed, page.top() - bleed,
                  page.width() * 0.46 + bleed, page.height() + bleed * 2)
    painter.fillRect(band, QColor(TOKENS["INK"]))
    # 액센트 한 줄 — 판의 오른쪽 모서리
    painter.fillRect(QRectF(band.right() - 3, band.top(), 3, band.height()),
                     QColor(TOKENS["ACC"]))

    pad = page.width() * 0.05
    inner = band.adjusted(pad, page.height() * 0.30, -pad, 0)
    f = QFont(painter.font())
    f.setPointSize(11)
    f.setBold(True)
    painter.setFont(f)
    painter.setPen(QColor(TOKENS["ACC_INK"]))
    painter.drawText(inner, Qt.AlignLeft | Qt.AlignTop, "ET REPORT")

    f.setPointSize(26)
    painter.setFont(f)
    painter.setPen(QColor(TOKENS["INK_TEXT"]))
    painter.drawText(inner.adjusted(0, page.height() * 0.06, 0, 0),
                     Qt.AlignLeft | Qt.AlignTop, "사용 설명서")

    f.setPointSize(10)
    f.setBold(False)
    painter.setFont(f)
    painter.setPen(QColor(TOKENS["INK_MUTED"]))
    lines = ["ET 계측 데이터 추출 · 분석 · 리포트"]
    if version:
        lines.append(f"버전 {version}")
    lines.append(f"{date.today():%Y-%m-%d} 기준")
    painter.drawText(inner.adjusted(0, page.height() * 0.20, 0, 0),
                     Qt.AlignLeft | Qt.AlignTop, "\n".join(lines))

    # 오른쪽 — 차례
    right = QRectF(band.right() + pad, page.top() + page.height() * 0.30,
                   page.width() - band.width() - pad * 2, page.height() * 0.6)
    f.setPointSize(9)
    painter.setFont(f)
    painter.setPen(QColor(TOKENS["MUTED"]))
    painter.drawText(right, Qt.AlignLeft | Qt.AlignTop, "차례")
    f.setPointSize(10)
    painter.setFont(f)
    painter.setPen(QColor(TOKENS["TEXT"]))
    toc = "\n".join(f"{i:02d}   {s.title}"
                    for i, s in enumerate(sections(), 1))
    painter.drawText(right.adjusted(0, page.height() * 0.045, 0, 0),
                     Qt.AlignLeft | Qt.AlignTop, toc)


def _section_page(painter, page, sec: Section, index: int, total: int,
                  version: str, QImage, QFont, QRectF, Qt) -> None:
    from PySide6.QtGui import QColor

    # 머리 — 번호(액센트) + 제목. 번호는 장식이 아니라 차례와 맞는 순서다.
    f = QFont(painter.font())
    f.setPointSize(17)
    f.setBold(True)
    painter.setFont(f)
    painter.setPen(QColor(TOKENS["ACC"]))
    num = f"{index:02d}"
    num_w = painter.fontMetrics().horizontalAdvance(num + "   ")
    painter.drawText(page, Qt.AlignLeft | Qt.AlignTop, num)
    painter.setPen(QColor(TOKENS["TEXT"]))
    painter.drawText(page.adjusted(num_w, 0, 0, 0), Qt.AlignLeft | Qt.AlignTop,
                     sec.title)

    rule_y = page.top() + int(page.height() * 0.055)
    painter.fillRect(QRectF(page.left(), rule_y, page.width(), 1),
                     QColor(TOKENS["RULE"]))

    f.setPointSize(10)
    f.setBold(False)
    painter.setFont(f)
    painter.setPen(QColor(TOKENS["TEXT"]))
    top = int(page.height() * 0.075)
    text_h = int(page.height() * (0.36 if sec.shot else 0.85))
    y = page.top() + top
    line_h = painter.fontMetrics().lineSpacing()
    if sec.body:
        painter.drawText(QRectF(page.left(), y, page.width(), text_h),
                         Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                         "\n".join(sec.body))
        y += line_h * len(sec.body)
    if sec.rows:
        y = _rows(painter, page, y + line_h * 0.4, sec.rows, QRectF, Qt, QFont)
    if sec.tail:
        painter.setFont(f)
        painter.setPen(QColor(TOKENS["TEXT"]))
        painter.drawText(QRectF(page.left(), y + line_h * 0.4, page.width(),
                                text_h),
                         Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                         "\n".join(sec.tail))

    _footer(painter, page, index, total, version, QRectF, Qt, QFont)

    if sec.shot is None or not Path(sec.shot).exists():
        return
    img = QImage(str(sec.shot))
    if img.isNull():
        log.warning("캡처를 읽지 못했습니다: %s", sec.shot)
        return
    area_top = page.top() + top + text_h + int(page.height() * 0.02)
    area_h = page.bottom() - area_top - int(page.height() * 0.08)
    scaled = img.scaled(page.width(), area_h, Qt.KeepAspectRatio,
                        Qt.SmoothTransformation)
    x = page.left() + (page.width() - scaled.width()) // 2
    painter.drawImage(x, area_top, scaled)
    painter.setPen(QColor(TOKENS["RULE"]))          # 캡처 가장자리 얇은 테두리
    painter.drawRect(QRectF(x, area_top, scaled.width(), scaled.height()))
    if sec.caption:
        f.setPointSize(8)
        painter.setFont(f)
        painter.setPen(QColor(TOKENS["MUTED"]))
        painter.drawText(QRectF(page.left(), area_top + scaled.height() + 6,
                                page.width(), 30),
                         Qt.AlignHCenter | Qt.AlignTop, sec.caption)


def _rows(painter, page, y: float, rows, QRectF, Qt, QFont) -> float:
    """'이름 → 설명' 표. 왼쪽 열은 굵게, 오른쪽은 줄바꿈을 그대로 살린다.

    공백 문자로 열을 맞추지 않는 이유: 본문 글꼴이 비례라 자릿수가 어긋난다.
    """
    from PySide6.QtGui import QColor

    f = QFont(painter.font())
    f.setPointSize(10)
    line_h = painter.fontMetrics().lineSpacing()
    key_w = page.width() * 0.17
    indent = page.width() * 0.02
    for key, val in rows:
        n = val.count("\n") + 1
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(TOKENS["ACC"]))
        painter.drawText(QRectF(page.left() + indent, y, key_w, line_h * n),
                         Qt.AlignLeft | Qt.AlignTop, key)
        f.setBold(False)
        painter.setFont(f)
        painter.setPen(QColor(TOKENS["TEXT"]))
        painter.drawText(
            QRectF(page.left() + indent + key_w, y,
                   page.width() - indent - key_w, line_h * n),
            Qt.AlignLeft | Qt.AlignTop, val)
        y += line_h * n + line_h * 0.25
    return y


def _footer(painter, page, index: int, total: int, version: str,
            QRectF, Qt, QFont) -> None:
    from PySide6.QtGui import QColor

    f = QFont(painter.font())
    f.setPointSize(8)
    f.setBold(False)
    painter.setFont(f)
    painter.setPen(QColor(TOKENS["MUTED"]))
    box = QRectF(page.left(), page.bottom() - page.height() * 0.035,
                 page.width(), page.height() * 0.035)
    left = "ET Report 사용 설명서" + (f"  ·  v{version}" if version else "")
    painter.drawText(box, Qt.AlignLeft | Qt.AlignBottom, left)
    painter.drawText(box, Qt.AlignRight | Qt.AlignBottom, f"{index} / {total}")
