"""사용자 설정 · 프리셋 저장소 (로컬 JSON).

두 종류의 프리셋을 저장한다 — UI 목업 v12와 1:1 대응.
- ExtractPreset : 데이터 워크스페이스. DB·리포메터·조건 묶음 (기간은 저장하지 않음)
- AnalysisConfig: 분석 워크스페이스. DB·Plot 템플릿·Table 템플릿·Report 이름 묶음
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dc_fields

from etreport.paths import settings_file, write_json_atomic

SCHEMA_VERSION = 3


def _fields(cls, raw: dict) -> dict:
    """dataclass가 아는 키만 추린다 — 구버전 settings.json 호환."""
    known = {f.name for f in dc_fields(cls)}
    return {k: v for k, v in raw.items() if k in known}


def _as_dict(v: object) -> dict:
    """설정 파일의 값이 dict일 때만 받는다 — 아니면 빈 dict."""
    return v if isinstance(v, dict) else {}


def _only(cls, raw: dict):
    return cls(**_fields(cls, raw))


@dataclass
class Condition:
    col: str
    val: str = ""
    mode: str = "auto"        # auto | regexp  (숫자·timestamp 컬럼은 auto 고정)
    required: bool = False    # line_id


@dataclass
class ExtractPreset:
    name: str
    db_path: str = ""
    reformatter_path: str = ""
    reformatter_sheet: str = ""       # 빈 값 = 첫 시트
    out_dir: str = ""
    conditions: list[Condition] = field(
        default_factory=lambda: [Condition("line_id", required=True)]
    )
    save_csv: bool = True
    save_sbdf: bool = False
    # 예약 실행(§13) — 작업 스케줄러에 등록해 둔 내용을 여기에도 남긴다.
    # 스케줄러가 진실이지만, 화면이 "무엇을 걸어 뒀는지" 보여 주려면 사본이 필요하다
    # (schtasks /Query로는 우리가 넣은 --days를 되읽기 번거롭다).
    schedule_enabled: bool = False
    schedule_freq: str = "DAILY"      # DAILY | WEEKLY | HOURLY
    schedule_at: str = "06:00"
    schedule_days: int = 1            # 추출 기간 — 오늘 포함 최근 N일
    schedule_interval: int = 6        # HOURLY일 때 몇 시간마다


@dataclass
class AnalysisConfig:
    name: str
    db_path: str = ""
    plot_template_path: str = ""
    table_template_path: str = ""
    reformatter_path: str = ""
    report: str = ""          # 템플릿의 Report 컬럼 값
    table_slide_mode: str = "overflow"  # overflow | split  (예전 값 "wide" = overflow)
    # 시트 이름 (빈 값 = 첫 시트)
    plot_sheet: str = ""
    table_sheet: str = ""
    reformatter_sheet: str = ""
    # 로그 축 판정은 plot 관심사 — 분석 설정에 속한다
    log_patterns: list[str] = field(default_factory=lambda: ["Ioff*", "*Leak*", "Jg*"])
    split_path: str = ""
    split_text: str = ""      # 붙여넣기로 넣은 실험 조건(§3.4)
    # fab tracking에서 뽑은 조건은 기준(REF) 코드가 'Base'가 아니다 —
    # 그대로 두면 [적용] 때 REF가 바뀌므로 함께 저장한다.
    split_baseline: str = ""
    # 기준(REF)으로 삼을 lot. 있으면 그 lot의 step별 다수 조건이 baseline이 된다
    # (§6.1) — split 실험은 lot 안에서도 조건이 갈리므로 코드 하나로는 못 적는다.
    split_baseline_lot: str = ""
    # plot에서 lot마다 심볼을 달리할지. 표시 옵션이라 DB를 다시 읽지 않는다.
    lot_split_symbols: bool = False
    # 이상치 필터(Tukey) — 표·plot을 그리기 전에 IQR의 k배 밖을 걸러 낸다.
    # 기본은 꺼짐: 데이터를 버리는 동작은 사용자가 켜야 시작된다.
    tukey_enabled: bool = False
    tukey_k: float = 3.0
    tukey_scope: str = "cond"      # cond(step·온도별) | all(item 전체)


@dataclass
class Settings:
    schema: int = SCHEMA_VERSION
    skipped_version: str | None = None          # 업데이트 건너뛰기
    extract_presets: list[ExtractPreset] = field(default_factory=list)
    analysis_configs: list[AnalysisConfig] = field(default_factory=list)
    last_extract_preset: str = ""
    last_analysis_config: str = ""
    dock_tools_open: bool = False               # 도크 [도구] 묶음 펼침 여부
    # DB 경로 → 그 DB에서 마지막으로 체크한 lot 목록(§9.2). 설정 프리셋이 아니라
    # 여기에 두는 이유는, 프리셋을 바꿔도 **같은 DB면 같은 lot을 보고 싶기** 때문이다.
    # 빈 목록은 '전부'와 같은 뜻이라 기록하지 않는다.
    lot_selections: dict[str, list[str]] = field(default_factory=dict)

    # ── 영속화 ────────────────────────────────────────────────
    def save(self) -> None:
        # 앱 종료 시점에도 호출된다 — 중간에 끊겨 파일이 깨지면 프리셋이 전부
        # 사라지므로 임시 파일에 쓰고 교체한다.
        write_json_atomic(settings_file(), asdict(self), indent=2)

    @classmethod
    def load(cls) -> Settings:
        f = settings_file()
        if not f.exists():
            return cls.defaults()
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls.defaults()
        raw.pop("schema", None)
        s = cls(
            skipped_version=raw.get("skipped_version"),
            last_extract_preset=raw.get("last_extract_preset", ""),
            last_analysis_config=raw.get("last_analysis_config", ""),
            dock_tools_open=bool(raw.get("dock_tools_open", False)),
            # 손으로 고친 설정 파일이 들어와도 앱이 죽지 않게 모양을 확인해 받는다
            # — dict가 아닌 값이 들어와도 통째로 버리고 넘어간다
            lot_selections={
                str(k): [str(x) for x in v]
                for k, v in _as_dict(raw.get("lot_selections")).items()
                if isinstance(v, list)},
        )
        # 버전 간 필드가 늘거나 줄어도 설정 파일 때문에 앱이 죽지 않도록,
        # 현재 dataclass가 아는 키만 남기고 나머지는 버린다.
        for p in raw.get("extract_presets", []):
            conds = [_only(Condition, c) for c in p.pop("conditions", [])]
            s.extract_presets.append(
                ExtractPreset(**_fields(ExtractPreset, p), conditions=conds))
        for c in raw.get("analysis_configs", []):
            s.analysis_configs.append(AnalysisConfig(**_fields(AnalysisConfig, c)))
        if not s.extract_presets:
            s.extract_presets = cls.defaults().extract_presets
        if not s.analysis_configs:
            s.analysis_configs = cls.defaults().analysis_configs
        return s

    @classmethod
    def defaults(cls) -> Settings:
        """첫 실행 — 바로 감을 잡을 수 있게 예시 조건을 채워 둔다."""
        s = cls()
        s.extract_presets.append(ExtractPreset(
            name="M2 정기 모니터링",
            conditions=[
                Condition("line_id", "L1", required=True),
                Condition("root_lot_id", "PA12* PB201 !PA125"),
                Condition("temperature", ">=25"),
            ]))
        s.extract_presets.append(ExtractPreset(
            name="신규 device 평가",
            conditions=[
                Condition("line_id", "L2", required=True),
                Condition("device_id", "ND*"),
                Condition("step_id", "M1 M2 M3"),
            ]))
        s.analysis_configs.append(AnalysisConfig(name="M2 정기 리포트",
                                                report="M2_ET"))
        s.analysis_configs.append(AnalysisConfig(name="신규 device 평가",
                                                report="DEV_EVAL"))
        return s

    # ── 이름으로 찾기 ─────────────────────────────────────────
    def extract_preset(self, name: str) -> ExtractPreset | None:
        return next((p for p in self.extract_presets if p.name == name), None)

    def analysis_config(self, name: str) -> AnalysisConfig | None:
        return next((c for c in self.analysis_configs if c.name == name), None)
