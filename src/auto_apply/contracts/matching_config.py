"""매칭 규칙(하드컷/트랙/스코어링) 설정의 타입 계약.

`config/matching.yaml`의 스키마다. 이 프로젝트 사용자의 개인 직무 취향이 담기는
자리이므로 값 자체는 코드가 아니라 데이터로 둔다 (§3 Recipe=데이터 원칙과 같은 이유).
`MatchingConfigSource` port 가 이 형태로 읽어 domain/job_screening.py 와
domain/job_applicability.py 에 그대로 넘긴다.
"""

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TrackRule(_Frozen):
    """지원 대상 직무 트랙 하나. 키워드가 맞으면 이 트랙으로 분류되고 가산점을 받는다."""

    label: str
    weight: int = 0
    keywords: list[str] = Field(default_factory=list)
    # 본문에서는 잡음이지만 제목/직무그룹에서는 결정적인 키워드.
    headline_keywords: list[str] = Field(default_factory=list)
    # 플랫폼이 붙인 직무그룹과 "정확히" 일치할 때만 센다 (부분일치 아님).
    categories: list[str] = Field(default_factory=list)
    # 이 트랙이 무력화하는 하드컷 코드 목록.
    overrides_hardcut: list[str] = Field(default_factory=list)


class HardcutRule(_Frozen):
    """하드컷 하나. 걸리면 LLM 호출 없이 즉시 제외된다."""

    label: str
    keywords: list[str] = Field(default_factory=list)
    # 이 키워드가 함께 있으면 하드컷을 취소한다.
    unless: list[str] = Field(default_factory=list)
    # 트랙 예외(overrides_hardcut)로도 못 넘는 신호. 걸리면 무조건 제외.
    always: list[str] = Field(default_factory=list)


class ScoringConfig(_Frozen):
    """적합도 가산점."""

    keywords_stack: list[str] = Field(default_factory=list)
    stack_bonus: int = 0
    stack_max: int = 0
    keywords_newbie: list[str] = Field(default_factory=list)
    newbie_bonus: int = 0
    keywords_english: list[str] = Field(default_factory=list)
    english_bonus: int = 0
    keywords_remote: list[str] = Field(default_factory=list)
    remote_bonus: int = 0
    keywords_domain: list[str] = Field(default_factory=list)
    domain_bonus: int = 0


class LocationConfig(_Frozen):
    preferred: list[str] = Field(default_factory=list)
    preferred_bonus: int = 0


class EssayConfig(_Frozen):
    autowrite: bool = False
    max_autowrite: int = 3


class ApplicabilityRules(_Frozen):
    """축 2 판정 기준값. `domain.job_applicability.evaluate()`가 읽는다."""

    min_fit_score: int = 60
    min_description_chars: int = 200
    max_required_gaps: int = 2
    min_days_left: int = 0
    essays: EssayConfig = Field(default_factory=EssayConfig)
    available_documents: list[str] = Field(default_factory=list)


class MatchingConfig(_Frozen):
    tracks: dict[str, TrackRule] = Field(default_factory=dict)
    hardcuts: dict[str, HardcutRule] = Field(default_factory=dict)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    location: LocationConfig = Field(default_factory=LocationConfig)
    applicability: ApplicabilityRules = Field(default_factory=ApplicabilityRules)
