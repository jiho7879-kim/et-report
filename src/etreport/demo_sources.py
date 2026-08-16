"""데모용 가짜 사내 소스 — bdq·boto3 없이 조회 경로를 그대로 밟는다.

데모 모드에서만 설치한다(`install()`). 바꾸는 것은 **데이터가 들어오는 입구
하나씩**뿐이다.

    extractor._fetch    Impala 조회      → 합성 long 프레임
    metrology.fetch     inline 계측      → 합성 계측 프레임
    fabtracking.fetch   fab tracking     → 합성 tracking 프레임
    s3.client           boto3 클라이언트 → 로컬 폴더를 쓰는 가짜 클라이언트

입구만 갈아 끼우므로 SQL 조립·스키마 정규화·청크 분할·리포메팅·적재·통계는
전부 실제 코드가 돈다. 조회 대상은 SQL에서 읽어 낸다 — 기간·item·lot을
바꿔 가며 눌러도 결과가 따라 움직여야 데모로 쓸모가 있다.
"""
from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

import polars as pl

from etreport import demo_data

log = logging.getLogger(__name__)

_installed = False
_ORIG: list[tuple] = []          # (모듈, 속성명, 원본) — uninstall이 되돌린다

_DATE_GE = re.compile(r"tkout_time\s*>=\s*'(\d{4}-\d{2}-\d{2})")
_DATE_LT = re.compile(r"tkout_time\s*<\s*'(\d{4}-\d{2}-\d{2})")
_IN_LIST = re.compile(r"(\w+)\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
_EQ_ONE = re.compile(r"(\w+)\s*=\s*'([^']*)'")
_LIKE = re.compile(r"(\w+)\s+LIKE\s+'([^']*)'", re.IGNORECASE)
_QUOTED = re.compile(r"'([^']*)'")
#: 시각은 따로 다루고, 나머지 문자열 조건은 이 목록의 컬럼만 반영한다.
_FILTER_COLS = ("line_id", "root_lot_id", "wafer_id", "step_id", "item_id")


def install(s3_root: str | Path | None = None) -> None:
    """가짜 소스를 붙인다(두 번 불러도 한 번만).

    바꾼 함수는 원본과 함께 기억해 둔다 — `uninstall()`로 되돌릴 수 있어야
    테스트가 서로를 오염시키지 않는다(데모 테스트가 S3 테스트를 망가뜨렸다).
    """
    global _installed
    if _installed:
        return
    from etreport.data import extractor, fabtracking, metrology, s3

    root = Path(s3_root) if s3_root else None
    _patch(extractor, "_fetch", fake_extract)
    _patch(metrology, "fetch", fake_metrology)
    _patch(fabtracking, "fetch", fake_tracking)
    _patch(s3, "client", lambda c, _root=root: FakeS3Client(_root))
    _installed = True
    log.info("데모 소스 설치 — 추출·계측·tracking·S3는 합성 데이터로 응답합니다")


def uninstall() -> None:
    """원래 함수로 되돌린다(설치한 적 없으면 아무 일도 하지 않는다)."""
    global _installed
    while _ORIG:
        module, name, original = _ORIG.pop()
        setattr(module, name, original)
    _installed = False


def _patch(module, name: str, fake) -> None:
    _ORIG.append((module, name, getattr(module, name)))
    setattr(module, name, fake)


# ── SQL에서 조건 읽어 내기 ───────────────────────────────────
def _in_values(sql: str, column: str) -> list[str]:
    """`<column> IN ('a', 'b')` 의 값들. 여러 절이면 모두 합친다."""
    out: list[str] = []
    for col, body in _IN_LIST.findall(sql):
        if col.lower() == column.lower():
            out += _QUOTED.findall(body)
    return out


def _dates(sql: str) -> tuple[str | None, str | None]:
    lo = _DATE_GE.search(sql)
    hi = _DATE_LT.search(sql)
    return (lo.group(1) if lo else None, hi.group(1) if hi else None)


def _like_regex(pat: str) -> str:
    """LIKE 패턴 → 정규식. `%`=여러 글자 · `_`=한 글자 · `\\`로 이스케이프."""
    out: list[str] = []
    i = 0
    while i < len(pat):
        ch = pat[i]
        if ch == "\\" and i + 1 < len(pat):
            out.append(re.escape(pat[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
        i += 1
    return "^" + "".join(out) + "$"


def apply_where(df: pl.DataFrame, sql: str) -> pl.DataFrame:
    """SQL의 **포함 조건**(`=` · `IN` · `LIKE`)을 프레임에 반영한다.

    조건 화면에서 lot·step을 좁히면 데모 결과도 좁아져야 한다. 제외(`NOT`)·
    정규식까지 흉내 내지는 않는다 — 가짜 소스의 목적은 조회 경로를 밟는 것이지
    Impala를 다시 만드는 것이 아니다.
    """
    want: dict[str, list[str]] = {}
    like: dict[str, list[str]] = {}
    for col, body in _IN_LIST.findall(sql):
        want.setdefault(col.lower(), []).extend(_QUOTED.findall(body))
    for col, val in _EQ_ONE.findall(sql):
        want.setdefault(col.lower(), []).append(val)
    for col, pat in _LIKE.findall(sql):
        like.setdefault(col.lower(), []).append(pat)

    for col in _FILTER_COLS:
        if col not in df.columns:
            continue
        conds = []
        if want.get(col):
            conds.append(pl.col(col).is_in(want[col]))
        for pat in like.get(col, []):
            conds.append(pl.col(col).str.contains(_like_regex(pat)))
        if conds:
            expr = conds[0]
            for c in conds[1:]:
                expr = expr | c
            df = df.filter(expr)
    return df


# ── 가짜 조회 ────────────────────────────────────────────────
def fake_extract(sql: str) -> pl.DataFrame:
    """ET 추출 — 기간과 item_id 목록으로 좁힌 long 프레임.

    실제 조회와 같은 컬럼·타입으로 돌려주므로 이후 `normalize_schema`부터는
    사내 PC와 완전히 같은 코드가 돈다. 조건이 하나도 안 맞으면 **빈 프레임**을
    돌려준다 — "결과 0행"도 데모로 확인해야 하는 상태다.
    """
    df = _long_cache()
    lo, hi = _dates(sql)
    if lo:
        df = df.filter(pl.col("tkout_time") >= datetime.fromisoformat(lo))
    if hi:
        df = df.filter(pl.col("tkout_time") < datetime.fromisoformat(hi))
    df = apply_where(df, sql)
    log.debug("데모 추출 응답 %d행 (%s ~ %s)", df.height, lo, hi)
    return df


def fake_metrology(sql: str) -> pl.DataFrame:
    """inline 계측 — 분석 중인 lot으로 좁힌 계측 프레임."""
    lots = _in_values(sql, "root_lot_id")
    df = apply_where(demo_data.metrology_frame(lots or None), sql)
    if (m := re.search(r"item_id\s+REGEXP\s+'([^']+)'", sql)):
        df = df.filter(pl.col("item_id").str.contains(f"({m.group(1)})"))
    return df


def fake_tracking(sql: str) -> pl.DataFrame:
    """fab tracking — lot을 주면 그 lot만."""
    lots = _in_values(sql, "root_lot_id")
    return apply_where(demo_data.tracking_frame(lots or None), sql)


_LONG: pl.DataFrame | None = None


def _long_cache() -> pl.DataFrame:
    """합성 raw는 한 번만 만든다 — 청크 4개가 동시에 부른다(bdq는 스레드 안전)."""
    global _LONG
    if _LONG is None:
        _LONG = demo_data.long_frame()
    return _LONG


# ── 가짜 S3 ──────────────────────────────────────────────────
class FakeS3Client:
    """로컬 폴더를 버킷처럼 쓰는 최소 구현.

    `data/s3.py`가 실제로 부르는 것만 갖춘다 — list_objects_v2(Delimiter로 한
    단계) · upload_file · download_file. 페이지네이션 분기까지 밟아 보도록
    한 번에 최대 `page_size`개만 돌려주고 이어받기 토큰을 준다.
    """

    page_size = 200

    def __init__(self, root: str | Path | None = None) -> None:
        from etreport.paths import appdata_dir
        self.root = Path(root) if root else appdata_dir() / "demo" / "_s3"
        self.root.mkdir(parents=True, exist_ok=True)

    def _seed(self, bucket: Path) -> None:
        """빈 버킷이면 그럴듯한 폴더 몇 개를 만들어 둔다(트리를 펼쳐 보게)."""
        if any(bucket.iterdir()):
            return
        for folder in ("8nm_sram/2026-08", "8nm_sram/공유", "17lpv"):
            (bucket / folder).mkdir(parents=True, exist_ok=True)
        (bucket / "8nm_sram" / "2026-08" / "readme.csv").write_text(
            "메모\n데모용 가짜 버킷입니다\n", encoding="utf-8")

    def _bucket(self, bucket: str) -> Path:
        p = self.root / (bucket or "demo-bucket")
        p.mkdir(parents=True, exist_ok=True)
        self._seed(p)
        return p

    def list_objects_v2(self, Bucket: str, Prefix: str = "",
                        Delimiter: str = "/",
                        ContinuationToken: str | None = None) -> dict:
        base = self._bucket(Bucket)
        start = base / Prefix if Prefix else base
        folders, files = [], []
        if start.is_dir():
            for p in sorted(start.iterdir()):
                if p.is_dir():
                    folders.append({"Prefix": f"{Prefix}{p.name}/"})
                else:
                    st = p.stat()
                    files.append({"Key": f"{Prefix}{p.name}", "Size": st.st_size,
                                  "LastModified": datetime.fromtimestamp(st.st_mtime)})
        off = int(ContinuationToken or 0)
        page = files[off:off + self.page_size]
        more = len(files) > off + self.page_size
        return {"CommonPrefixes": folders if off == 0 else [],
                "Contents": page, "IsTruncated": more,
                "NextContinuationToken": str(off + self.page_size)}

    def upload_file(self, local: str, bucket: str, key: str) -> None:
        dest = self._bucket(bucket) / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, dest)

    def download_file(self, bucket: str, key: str, out: str) -> None:
        src = self._bucket(bucket) / key
        if not src.is_file():
            raise FileNotFoundError(f"버킷에 없는 키입니다: {key}")
        shutil.copy2(src, out)
