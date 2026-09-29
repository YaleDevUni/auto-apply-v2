"""LLM 구조화 출력 스키마 (ARCHITECTURE.md §8). `LLMClient.structured()`가 강제하는 계약.

어떤 오케스트레이션 프레임워크에도 묶이지 않는다 — 순수 Pydantic이라 나중에 LangGraph든
PydanticAI든 같은 스키마를 그대로 재사용할 수 있다 (§9.2).
"""

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResumeHighlight(_Frozen):
    """이력서 서술 한 줄. `fact_ids`가 근거다 — review 게이트가 이걸로 근거 여부를 판정한다."""

    text: str
    fact_ids: list[str] = Field(default_factory=list)


class BlockBullets(_Frozen):
    """경력/프로젝트 블록 하나에 대한 LLM 서술.

    `block_id`는 프롬프트에 준 블록 id(domain/resume_matching.py의 FactBlock.id)를 그대로
    인용해야 한다 — 회사명·기간·기술스택은 LLM 이 만들지 않는다.
    """

    block_id: str
    bullets: list[ResumeHighlight] = Field(default_factory=list)


class ResumeContentSchema(_Frozen):
    summary: str
    highlights: list[ResumeHighlight] = Field(default_factory=list)
    blocks: list[BlockBullets] = Field(default_factory=list)
    ai_usage: list[ResumeHighlight] = Field(default_factory=list)
    # 이 공고/이 지원 건에 대해 주관적으로 판단한 주의사항 — 승인 전 사람에게 그대로
    # 노출된다. 근거 fact_id 가 필요한 서술이 아니라 공고 본문에 대한 메타 코멘트라
    # ground_check 가 검증하지 않는다.
    caution_notes: list[str] = Field(default_factory=list)
