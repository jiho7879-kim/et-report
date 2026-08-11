# 새 저장소에 올리기

## 1) 저장소 생성 (둘 중 하나)
**웹**: https://github.com/new → Owner `jiho7879-kim`, 이름 `et-report`,
**Private 선택**, README/.gitignore는 추가하지 않음(이미 있음)

**gh CLI**:
```bash
gh auth login
gh repo create jiho7879-kim/et-report --private --source=. --remote=origin --push
```

## 2) 최초 푸시 (웹으로 만든 경우)
```bash
cd et_report
git init -b main
git add .
git commit -m "ET Report Tool 초기 구조 — 추출/분석/PPT + 자동 업데이트"
git remote add origin https://github.com/jiho7879-kim/et-report.git
git push -u origin main
```

## 3) 올리기 전 확인 — 사내 식별자
아래 값들은 사내 환경 정보입니다. 공개 저장소로 올릴 계획이면 먼저 바꾸세요.

| 파일 | 내용 |
|---|---|
| `update/checker.py` | `API_BASE`(사내 GHE 주소), `OWNER`, `REPO` |
| `build/build_release.py` | `--repo pde-tools/et-report` |
| `config/catalog.py`, `data/querybuilder.py` | 테이블명 `eds.f_et_test` |
| `app.py`, `catalog.py`, `extractor.py` | 사내 패키지 `bigdataquery` |
| `docs/` | 목업·계획서에 사내 item alias·step ID 다수 |

환경변수로 빼두면 공개해도 안전합니다:
```python
API_BASE = os.environ.get("ETREPORT_GHE_API", "https://api.github.com")
TABLE    = os.environ.get("ETREPORT_TABLE", "eds.f_et_test")
```

`.gitignore`에 `*.duckdb`, `*.parquet`, `*.xlsx`, `settings.json` 등이
이미 들어 있어 실제 계측 데이터는 커밋되지 않습니다.
