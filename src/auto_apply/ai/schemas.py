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


class ResumeContentSchema(_Frozen):
    summary: str
    highlights: list[ResumeHighlight] = Field(default_factory=list)
