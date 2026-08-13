"""S3 창 — 로그인(4항목) + 폴더 트리 + 업로드/다운로드 (기능 C).

한 창에서 끝난다: 위에서 namespace·bucket·AccessKey·Secret을 넣고 [연결],
가운데 트리에서 폴더를 펼치고, 아래 버튼으로 올리거나 내린다.

느린 작업(연결·목록·전송)은 전부 `run_in_background`로 넘긴다 — 사내망
왕복이 수 초씩 걸리는데 그동안 창이 얼면 곤란하다.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from etreport.data import s3
from etreport.ui.widgets.worker import run_in_background

FOLDER_ROLE = Qt.UserRole + 1          # 트리 아이템에 담는 폴더 경로


class S3Dialog(QDialog):
    def __init__(self, parent=None, default_dir: str = "") -> None:
        super().__init__(parent)
        self.cred = s3.load_credentials()
        self.default_dir = default_dir
        self.setWindowTitle("S3 저장소")
        self.resize(720, 620)

        v = QVBoxLayout(self)
        self.fields: dict[str, QLineEdit] = {}
        for key, label, secret in (("namespace", "Namespace name", False),
                                   ("bucket", "Bucket name", False),
                                   ("access_key", "AccessKey ID", False),
                                   ("secret_key", "Secret Access Key", True)):
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setFixedWidth(140)
            row.addWidget(lab)
            ed = QLineEdit(getattr(self.cred, key))
            if secret:
                ed.setEchoMode(QLineEdit.Password)
            self.fields[key] = ed
            row.addWidget(ed, 1)
            v.addLayout(row)

        bar = QHBoxLayout()
        self.chk_remember = QCheckBox("이 PC에 저장 (사용자 폴더, 소유자만 읽기)")
        self.chk_remember.setChecked(self.cred.remember)
        self.chk_remember.setToolTip(
            "settings.json이 아니라 %APPDATA%\\ETReport\\s3_credentials.json에\n"
            "따로 저장합니다. 끄면 이 창을 닫을 때 지웁니다.")
        bar.addWidget(self.chk_remember)
        bar.addStretch(1)
        b = QPushButton("연결")
        b.clicked.connect(self._connect)
        bar.addWidget(b)
        v.addLayout(bar)

        self.lbl = QLabel("AccessKey를 넣고 [연결]을 누르세요")
        self.lbl.setObjectName("hint")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["이름", "크기", "수정"])
        self.tree.itemExpanded.connect(self._expand)
        v.addWidget(self.tree, 1)

        tools = QHBoxLayout()
        for text, fn, tip in (
                ("업로드…", self._upload, "고른 폴더에 duckdb·csv·sbdf를 올립니다"),
                ("다운로드…", self._download, "고른 파일을 내려받습니다"),
                ("새로 고침", self._refresh, "")):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(fn)
            tools.addWidget(btn)
        tools.addStretch(1)
        v.addLayout(tools)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.accept)
        v.addWidget(bb)

    # ── 자격 증명 ────────────────────────────────────────────
    def collect(self) -> s3.S3Credentials:
        c = s3.S3Credentials(**{k: ed.text().strip()
                                for k, ed in self.fields.items()})
        c.remember = self.chk_remember.isChecked()
        return c

    def _connect(self) -> None:
        c = self.collect()
        if not c.ok():
            self.lbl.setText("Bucket·AccessKey·Secret은 모두 필요합니다")
            return
        self.cred = c
        s3.save_credentials(c)              # remember=False면 지운다
        self.lbl.setText("연결 중…")
        run_in_background(self, "S3 연결", lambda: s3.check(c),
                          done=self._connected)

    def _connected(self, msg: str) -> None:
        self.lbl.setText(str(msg))
        self._refresh()

    # ── 폴더 트리 ────────────────────────────────────────────
    def _refresh(self) -> None:
        if not self.cred.ok():
            return
        self.tree.clear()
        root = QTreeWidgetItem(self.tree, ["/", "", ""])
        root.setData(0, FOLDER_ROLE, "")
        self.tree.setCurrentItem(root)
        self._fill(root, "")
        root.setExpanded(True)

    def _expand(self, item: QTreeWidgetItem) -> None:
        if item.childCount() == 1 and item.child(0).text(0) == "…":
            item.takeChildren()
            self._fill(item, item.data(0, FOLDER_ROLE) or "")

    def _fill(self, parent: QTreeWidgetItem, path: str) -> None:
        c = self.cred

        def done(res) -> None:
            folders, files = res
            for name in folders:
                sub = QTreeWidgetItem(parent, [name + "/", "", ""])
                sub.setData(0, FOLDER_ROLE, f"{path}/{name}".strip("/"))
                QTreeWidgetItem(sub, ["…", "", ""])     # 펼칠 때 채운다
            for f in files:
                kb = f"{f['size'] / 1024:,.0f} KB" if f["size"] else ""
                mod = str(f.get("modified") or "")[:16]
                leaf = QTreeWidgetItem(parent, [f["name"], kb, mod])
                leaf.setData(0, FOLDER_ROLE, None)      # 파일 표시
            self.lbl.setText(f"{path or '/'} · 폴더 {len(folders)} · 파일 {len(files)}")

        run_in_background(self, "목록 조회",
                          lambda: s3.list_folder(c, path), done=done)

    def current_folder(self) -> str:
        it = self.tree.currentItem()
        while it is not None:
            data = it.data(0, FOLDER_ROLE)
            if data is not None:
                return str(data)
            it = it.parent()
        return ""

    def current_file(self) -> tuple[str, str] | None:
        it = self.tree.currentItem()
        if it is None or it.data(0, FOLDER_ROLE) is not None:
            return None
        parent = it.parent()
        folder = "" if parent is None else str(parent.data(0, FOLDER_ROLE) or "")
        return it.text(0), folder

    # ── 전송 ─────────────────────────────────────────────────
    def _upload(self) -> None:
        if not self.cred.ok():
            self.lbl.setText("먼저 [연결]하세요")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "올릴 파일", self.default_dir,
            "데이터 (*.duckdb *.csv *.sbdf *.parquet);;모든 파일 (*)")
        if not paths:
            return
        folder = self.current_folder()
        c = self.cred

        def work():
            return [s3.upload(c, p, folder) for p in paths]

        def done(keys) -> None:
            QMessageBox.information(self, "업로드", "\n".join(keys))
            self._refresh()

        run_in_background(self, "업로드", work, done=done)

    def _download(self) -> None:
        picked = self.current_file()
        if picked is None:
            self.lbl.setText("내려받을 파일을 고르세요")
            return
        name, folder = picked
        dest = QFileDialog.getExistingDirectory(self, "저장할 폴더",
                                                self.default_dir)
        if not dest:
            return
        c = self.cred

        def done(path) -> None:
            QMessageBox.information(self, "다운로드", str(path))

        run_in_background(self, "다운로드",
                          lambda: s3.download(c, name, folder, dest), done=done)

    def accept(self) -> None:                 # 닫을 때 저장 정책을 한 번 더 반영
        s3.save_credentials(self.collect())
        super().accept()


def default_download_dir() -> str:
    return str(Path.home())
