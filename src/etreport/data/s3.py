"""사내 S3 호환 스토리지 — duckdb·csv·sbdf 업로드/다운로드 (기능 C).

boto3로 endpoint를 직접 지정해 붙는다. 인증은 개인별 API key(AccessKey/Secret).

**Secret은 settings.json에 평문으로 두지 않는다.** 자격 증명은
`%APPDATA%\\ETReport\\s3_credentials.json`에 따로 두고 파일 권한을 소유자만
읽도록 좁힌다(POSIX 0600, Windows는 상속 권한 그대로 — OS 계정이 곧 경계다).
저장 여부는 사용자가 창에서 고른다. 더 강한 보관이 필요하면 OS 자격 증명
저장소(keyring)를 쓰도록 `load/save`만 갈아 끼우면 된다.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from dataclasses import fields as dc_fields
from pathlib import Path

from etreport.paths import appdata_dir, write_json_atomic

log = logging.getLogger(__name__)

ENDPOINT = "http://s3.daaplatform.samsungds.net:9020"
CRED_FILE = "s3_credentials.json"
#: 이 툴이 다루는 형식 — 그 외 확장자는 그대로 올리고 내린다.
FORMATS = (".duckdb", ".csv", ".sbdf", ".parquet")


@dataclass
class S3Credentials:
    namespace: str = ""
    bucket: str = ""
    access_key: str = ""
    secret_key: str = ""
    endpoint: str = ENDPOINT
    remember: bool = False          # 끄면 secret을 파일에 남기지 않는다

    def ok(self) -> bool:
        return bool(self.bucket and self.access_key and self.secret_key)


def cred_file() -> Path:
    return appdata_dir() / CRED_FILE


def save_credentials(c: S3Credentials) -> Path | None:
    """자격 증명 저장. remember=False면 저장하지 않고 기존 파일을 지운다."""
    f = cred_file()
    if not c.remember:
        f.unlink(missing_ok=True)
        return None
    write_json_atomic(f, asdict(c), indent=1)
    try:
        os.chmod(f, 0o600)          # 소유자만 읽기 — POSIX에서만 의미가 있다
    except OSError as e:            # Windows는 ACL 상속을 그대로 둔다
        log.debug("자격 증명 파일 권한 설정 생략: %s", e)
    return f


def load_credentials() -> S3Credentials:
    """저장된 자격 증명. 없거나 깨졌으면 빈 값으로 시작한다(§10.8)."""
    f = cred_file()
    if not f.exists():
        return S3Credentials()
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("자격 증명 파일을 읽지 못해 새로 입력받습니다")
        return S3Credentials()
    known = {fl.name for fl in dc_fields(S3Credentials)}
    return S3Credentials(**{k: v for k, v in raw.items() if k in known})


# ── 클라이언트 ───────────────────────────────────────────────
def client(c: S3Credentials):
    """boto3 S3 클라이언트. boto3가 없으면 ImportError를 그대로 올린다."""
    import boto3  # 선택 의존성 — 없는 PC도 있다

    return boto3.client(
        "s3", endpoint_url=c.endpoint or ENDPOINT,
        aws_access_key_id=c.access_key, aws_secret_access_key=c.secret_key)


def _prefix(namespace: str, path: str = "") -> str:
    """namespace를 접두어로 붙인 키. namespace가 비면 버킷 루트를 그대로 쓴다."""
    parts = [p.strip("/") for p in (namespace, path) if p and p.strip("/")]
    return "/".join(parts)


def list_folder(c: S3Credentials, path: str = "", cli=None) -> tuple[list[str], list[dict]]:
    """한 폴더의 (하위 폴더, 파일) — 트리를 한 단계씩 펼치기 위한 목록.

    S3에는 폴더가 없고 접두어만 있으므로 Delimiter로 한 단계만 끊어 읽는다
    (전체를 재귀로 훑으면 큰 버킷에서 몇 분씩 걸린다).
    """
    cli = cli or client(c)
    prefix = _prefix(c.namespace, path)
    if prefix:
        prefix += "/"
    folders: list[str] = []
    files: list[dict] = []
    token = None
    while True:
        kw = {"Bucket": c.bucket, "Prefix": prefix, "Delimiter": "/"}
        if token:
            kw["ContinuationToken"] = token
        res = cli.list_objects_v2(**kw)
        for cp in res.get("CommonPrefixes", []):
            folders.append(cp["Prefix"][len(prefix):].rstrip("/"))
        for obj in res.get("Contents", []):
            name = obj["Key"][len(prefix):]
            if not name or name.endswith("/"):
                continue            # 폴더 표식용 빈 객체
            files.append({"name": name, "size": obj.get("Size", 0),
                          "modified": obj.get("LastModified")})
        if not res.get("IsTruncated"):
            break
        token = res.get("NextContinuationToken")
    return sorted(folders), sorted(files, key=lambda f: f["name"])


def upload(c: S3Credentials, local: str | Path, folder: str = "",
           cli=None) -> str:
    """파일 하나를 폴더에 올린다. 올린 키를 반환."""
    p = Path(local)
    if not p.is_file():
        raise FileNotFoundError(f"올릴 파일이 없습니다: {p}")
    key = _prefix(c.namespace, f"{folder}/{p.name}" if folder else p.name)
    (cli or client(c)).upload_file(str(p), c.bucket, key)
    log.info("S3 업로드: %s → %s/%s", p.name, c.bucket, key)
    return key


def download(c: S3Credentials, name: str, folder: str, dest_dir: str | Path,
             cli=None) -> Path:
    """폴더의 파일 하나를 내려받는다. 저장한 경로를 반환."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    key = _prefix(c.namespace, f"{folder}/{name}" if folder else name)
    out = dest / Path(name).name
    (cli or client(c)).download_file(c.bucket, key, str(out))
    log.info("S3 다운로드: %s/%s → %s", c.bucket, key, out)
    return out


def check(c: S3Credentials, cli=None) -> str:
    """연결 확인 — 버킷 목록을 한 번 읽어 본다. 실패는 예외로 올린다."""
    cli = cli or client(c)
    cli.list_objects_v2(Bucket=c.bucket, Prefix=_prefix(c.namespace),
                        MaxKeys=1)
    return f"{c.bucket} 연결됨"
