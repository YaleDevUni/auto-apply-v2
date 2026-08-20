"""LLM 구조화 출력 스키마 (ARCHITECTURE.md §8). `LLMClient.structured()`가 강제하는 계약.

어떤 오케스트레이션 프레임워크에도 묶이지 않는다 — 순수 Pydantic이라 나중에 LangGraph든
PydanticAI든 같은 스키마를 그대로 재사용할 수 있다 (§9.2).
"""

from pydantic import BaseModel, ConfigDict, Field

from auto_apply.contracts.recipe import Action


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
    # 프롬프트가 제시한 카테고리 라벨 중 하나를 그대로 인용해야 한다(새 라벨을 만들지 않는다).
    # 확신이 없으면 빈 문자열로 둔다 — 포트폴리오 파일명으로 바꾸는 건 코드가 한다
    # (adapters/resume/_assemble.py, "AI는 생성만, 판정·조합은 코드").
    job_category: str = ""


class GuidePatchItem(_Frozen):
    """치환 쌍 하나. `old`는 가이드 본문에서 정확히 그대로 인용해야 한다

    (활동 계층이 문자열 일치로 검증한다).
    """

    old: str
    new: str
    rationale: str = ""


class GuidePatchSchema(_Frozen):
    """이력서 가이드 치환 제안 목록. 전문을 다시 쓰게 하지 않는다(domain/guide_patch.py 참고) —

    사용자 피드백 한 번에 서로 다른 지시가 여러 개 섞여 있을 수 있어 `patches`를 리스트로
    받는다 — 스키마가 항목 하나만 표현하면 다지시 피드백 중 일부가 조용히 누락된다
    (메모리 resume-revise-feedback-design 라이브 테스트로 실측).
    """

    patches: list[GuidePatchItem] = Field(min_length=1)


class RecipeDiffSchema(_Frozen):
    """recipe 수선 제안(§2.4 node B). `actions`가 `contracts.recipe.Action`을 그대로 재사용하는

    이유 — `Action`의 model_validator(selector 필요 여부 등)가 `LLMClient.structured()`의
    `model_validate()` 경유로 이미 실행된다(§2.4 node C "Pydantic 스키마 검증"이 재프롬프트
    루프 안에서 공짜로 딸려온다). `expected_elements`/`validation_rules`는 LLM이 건드리지
    않는다 — 폼 자체의 정체성이라 이전 recipe 값을 코드가 그대로 들고 간다
    (domain/recipe_repair.py, "AI는 생성만, 조합은 코드").
    """

    actions: list[Action] = Field(min_length=1, max_length=120)
    success_signals: list[str] = Field(min_length=1)
    rationale: str = ""
