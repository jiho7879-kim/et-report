"""기능 C — S3 저장소(로그인·폴더 목록·업로드/다운로드).

boto3는 선택 의존성이고 사내망도 없으므로 **가짜 클라이언트**를 주입해
키 조립·페이지네이션·자격 증명 보관 규칙을 검증한다. 실제 전송은 사내 PC에서
확인할 몫이고, 여기서 고정하는 것은 "우리가 만드는 키와 저장 규칙"이다.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from etreport.data import s3


class FakeS3:
    """list_objects_v2 / upload_file / download_file만 흉내낸다."""

    def __init__(self, keys: list[str] | None = None, page: int = 100) -> None:
        self.keys = keys or []
        self.page = page
        self.uploaded: list[tuple[str, str, str]] = []
        self.downloaded: list[tuple[str, str, str]] = []
        self.calls: list[dict] = []

    def list_objects_v2(self, **kw):
        self.calls.append(kw)
        prefix, delim = kw.get("Prefix", ""), kw.get("Delimiter")
        hit = [k for k in self.keys if k.startswith(prefix)]
        folders, files = set(), []
        for k in hit:
            rest = k[len(prefix):]
            if delim and delim in rest:
                folders.add(prefix + rest.split(delim)[0] + delim)
            else:
                files.append({"Key": k, "Size": 10, "LastModified": "2026-08-13"})
        start = 0
        if kw.get("ContinuationToken"):
            start = int(kw["ContinuationToken"])
        chunk = files[start:start + self.page]
        out = {"CommonPrefixes": [{"Prefix": p} for p in sorted(folders)],
               "Contents": chunk}
        if start + self.page < len(files):
            out["IsTruncated"] = True
            out["NextContinuationToken"] = str(start + self.page)
        return out

    def upload_file(self, local, bucket, key):
        self.uploaded.append((local, bucket, key))

    def download_file(self, bucket, key, dest):
        self.downloaded.append((bucket, key, dest))
        with open(dest, "w", encoding="utf-8") as f:
            f.write("x")


def _cred(**kw) -> s3.S3Credentials:
    base = {"namespace": "ns1", "bucket": "b1", "access_key": "AK",
            "secret_key": "SK"}
    return s3.S3Credentials(**{**base, **kw})


# ── 자격 증명 ────────────────────────────────────────────────
def test_credentials_are_not_stored_unless_asked(appdata):
    """★ Secret은 기본적으로 파일에 남기지 않는다."""
    assert s3.save_credentials(_cred(remember=False)) is None
    assert not s3.cred_file().exists()
    assert s3.load_credentials().secret_key == ""


def test_credentials_go_to_their_own_file_not_settings(appdata):
    from etreport.paths import settings_file

    path = s3.save_credentials(_cred(remember=True))

    assert path == s3.cred_file() != settings_file()
    assert json.loads(path.read_text(encoding="utf-8"))["bucket"] == "b1"
    got = s3.load_credentials()
    assert got.access_key == "AK" and got.remember


@pytest.mark.skipif(os.name == "nt", reason="POSIX 권한만 확인")
def test_credential_file_is_owner_only(appdata):
    path = s3.save_credentials(_cred(remember=True))
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"권한이 {oct(mode)} — 소유자만 읽어야 한다"


def test_turning_remember_off_deletes_the_file(appdata):
    s3.save_credentials(_cred(remember=True))
    assert s3.cred_file().exists()

    s3.save_credentials(_cred(remember=False))
    assert not s3.cred_file().exists()


def test_broken_credential_file_starts_empty(appdata):
    s3.cred_file().write_text("{망가진", encoding="utf-8")
    assert s3.load_credentials().bucket == ""       # 죽지 않는다(§10.8)


def test_unknown_keys_are_ignored(appdata):
    from etreport.paths import write_json_atomic
    write_json_atomic(s3.cred_file(), {"bucket": "b9", "미래필드": 1})
    assert s3.load_credentials().bucket == "b9"


def test_credentials_need_all_three():
    assert not s3.S3Credentials(bucket="b").ok()
    assert _cred().ok()


# ── 폴더 목록 ────────────────────────────────────────────────
def test_list_folder_splits_one_level():
    """★ 한 단계씩만 펼친다 — 큰 버킷을 재귀로 훑지 않는다."""
    cli = FakeS3(["2026/08/a.duckdb", "2026/08/b.csv",
                  "2026/07/c.csv", "root.csv"])

    folders, files = s3.list_folder(_cred(), "", cli=cli)

    assert folders == ["2026"]
    assert [f["name"] for f in files] == ["root.csv"]
    assert cli.calls[0]["Delimiter"] == "/"
    assert cli.calls[0]["Prefix"] == ""


def test_namespace_is_not_a_key_prefix():
    """★ 사내 버킷은 namespace가 아니라 **버킷 바로 아래** 폴더로 나뉜다.

    예전에는 namespace를 무조건 접두어로 붙여 조회해서 트리가 통째로 비었다.
    """
    cli = FakeS3(["8nm_sram/a.csv", "17lpv/b.csv"])

    folders, _files = s3.list_folder(_cred(namespace="ns1"), "", cli=cli)

    assert folders == ["17lpv", "8nm_sram"]
    assert cli.calls[0]["Prefix"] == ""


class NamespacedS3(FakeS3):
    """루트 목록이 비어 있고 <namespace>/ 아래에서만 보이는 버킷."""

    def list_objects_v2(self, **kw):
        if not kw.get("Prefix"):
            self.calls.append(kw)
            return {"CommonPrefixes": [], "Contents": []}
        return super().list_objects_v2(**kw)


def test_namespace_prefix_is_used_only_when_root_is_empty():
    """루트가 비어 있을 때만 <namespace>/를 접두어로 쓴다 — 연결할 때 감지."""
    cli = NamespacedS3(["ns1/2026/a.csv"])
    c = _cred()

    assert s3.detect_root_prefix(c, cli=cli) == "ns1"
    s3.check(c, cli=cli)                       # check가 c.root_prefix를 채운다

    assert c.root_prefix == "ns1"
    folders, _ = s3.list_folder(c, "", cli=cli)
    assert folders == ["2026"]


def test_root_listing_wins_over_namespace():
    """루트에 폴더가 보이면 접두어는 없다 — 사내 버킷의 실제 모습."""
    cli = FakeS3(["ns1/2026/a.csv"])
    assert s3.detect_root_prefix(_cred(), cli=cli) == ""


def test_list_folder_descends():
    cli = FakeS3(["2026/08/a.duckdb", "2026/08/b.csv"])
    folders, files = s3.list_folder(_cred(), "2026/08", cli=cli)

    assert folders == []
    assert [f["name"] for f in files] == ["a.duckdb", "b.csv"]


def test_list_folder_follows_pagination():
    cli = FakeS3([f"f{i}.csv" for i in range(250)], page=100)
    _folders, files = s3.list_folder(_cred(), "", cli=cli)
    assert len(files) == 250


def test_namespace_may_be_empty():
    cli = FakeS3(["top.csv"])
    _f, files = s3.list_folder(_cred(namespace=""), "", cli=cli)
    assert [f["name"] for f in files] == ["top.csv"]
    assert cli.calls[0]["Prefix"] == ""


# ── 업로드 · 다운로드 ────────────────────────────────────────
@pytest.mark.parametrize("ext", [".duckdb", ".csv", ".sbdf", ".parquet"])
def test_upload_builds_key_from_folder(tmp_path, ext):
    """★ duckdb·csv·sbdf 전부 같은 경로 규칙으로 올라간다."""
    f = tmp_path / f"result{ext}"
    f.write_text("x", encoding="utf-8")
    cli = FakeS3()

    key = s3.upload(_cred(), f, "2026/08", cli=cli)

    assert key == f"2026/08/result{ext}"
    assert cli.uploaded == [(str(f), "b1", key)]


def test_upload_uses_detected_root_prefix(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("x", encoding="utf-8")
    assert s3.upload(_cred(root_prefix="ns1"), f, "2026",
                     cli=FakeS3()) == "ns1/2026/a.csv"


def test_upload_to_root_folder(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("x", encoding="utf-8")
    assert s3.upload(_cred(), f, "", cli=FakeS3()) == "a.csv"


def test_upload_missing_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        s3.upload(_cred(), tmp_path / "없음.csv", cli=FakeS3())


def test_download_writes_into_chosen_folder(tmp_path):
    cli = FakeS3()
    out = s3.download(_cred(), "a.duckdb", "2026/08", tmp_path, cli=cli)

    assert out == tmp_path / "a.duckdb" and out.exists()
    assert cli.downloaded == [("b1", "2026/08/a.duckdb", str(out))]


def test_check_touches_the_bucket():
    cli = FakeS3(["a.csv"])
    msg = s3.check(_cred(), cli=cli)
    assert "b1" in msg and "파일 1" in msg
    assert cli.calls[0]["Delimiter"] == "/"


def test_client_needs_boto3():
    """boto3가 없으면 ImportError를 그대로 올린다 — UI가 안내로 바꾼다."""
    try:
        import boto3  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError):
            s3.client(_cred())
    else:                                              # 설치돼 있으면 생성만 확인
        assert s3.client(_cred()) is not None


# ── UI (모달 없이 조립만) ────────────────────────────────────
def test_dialog_builds_and_collects(appdata):
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:                                # pragma: no cover
        pytest.skip("PySide6 없음")
    from etreport.ui.widgets.s3_dialog import S3Dialog

    QApplication.instance() or QApplication([])
    dlg = S3Dialog()
    for key, val in (("namespace", "ns1"), ("bucket", "b1"),
                     ("access_key", "AK"), ("secret_key", "SK")):
        dlg.fields[key].setText(val)

    got = dlg.collect()

    assert got.ok() and got.namespace == "ns1"
    assert not got.remember                            # 기본은 저장 안 함
    assert set(dlg.fields) == {"namespace", "bucket", "access_key",
                               "secret_key"}
    dlg.deleteLater()
