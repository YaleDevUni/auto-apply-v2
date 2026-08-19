"""Fact — 이력서 생성의 유일한 사실 원천 (ARCHITECTURE.md §4).

Recipe/MatchingConfig 와 같은 이유로 코드가 아니라 데이터다: `config/facts.yaml`이 원본이고
이 파일은 그 스키마만 정의한다. `keywords`는 TrackRule.keywords 와 같은 이유로 둔다 — 자유
텍스트(content)를 job 설명과 직접 매칭하면 실패하기 쉬워서, 매칭용 신호를 명시적 필드로 뺐다.

`entity`/`block` 계열 필드는 이력서의 경력·프로젝트 섹션을 회사(또는 프로젝트) → 하위 블록
구조로 조립하기 위한 그룹핑 키다(domain/resume_matching.py의 group_facts_for_resume). 회사·
기간·블록 제목은 전부 이 필드에서 결정론적으로 나온다 — LLM 은 그룹 안의 fact 를 근거로
불릿 문장만 쓴다. kind가 experience/project가 아니거나 이 그룹핑에 참여하지 않는 fact(skill,
education, profile 등)는 전부 None 으로 둔다.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fact(_Frozen):
    id: str
    user_id: str
    kind: str
    content: str
    keywords: list[str] = Field(default_factory=list)
    source: str = ""
    verified_at: datetime | None = None

    # ── 이력서 경력/프로젝트 블록 그룹핑 (kind=experience|project 에서만 사용) ──
    entity: str | None = None
    """회사 또는 개인 프로젝트 단위 그룹 키 (예: "gtc", "proj-factory")."""
    entity_label: str | None = None
    """엔티티 헤더 표시명. 경력이면 "회사명(직무)" 형태 (예: "Acme(풀스택 개발자 인턴)")."""
    entity_period: str | None = None
    """엔티티 전체 기간 표시 (예: "2023.08 - 2024.04"). 그룹 안 fact 중 하나에만 있으면 된다."""
    block: str | None = None
    """entity 안의 하위 블록 키. None 이면 블록을 이루지 않고 entity 메타데이터만 제공한다
    (예: 경력의 역할 개요 fact)."""
    block_label: str | None = None
    """하위 블록 제목 (예: "SaaS 풀스택 개발 및 운영")."""
    block_period: str | None = None
    """하위 블록 기간 표시 (예: "2023.08 - 2024.04")."""
