# 동봉 한글 폰트 (선택)

여기에 `.ttf` / `.otf` / `.ttc` 를 넣으면 **시스템에 설치된 폰트보다 먼저**
쓰이고, PyInstaller 빌드에도 자동으로 포함됩니다
(`build/build_release.py` → `--add-data`).

폰트가 안 깔린 리눅스 PC에 배포해야 할 때만 넣으면 됩니다. 사내 PC(Windows)는
맑은 고딕이 이미 있으므로 비워 둬도 됩니다.

권장: Noto Sans KR 또는 나눔고딕 (OFL 라이선스 — 재배포 시 `OFL.txt` 동봉).
로직은 `src/etreport/fonts.py` 참조.
