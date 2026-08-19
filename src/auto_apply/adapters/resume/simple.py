"""plain 함수 기반 이력서 생성/검토 (ARCHITECTURE.md §2.3, §9.2).

파이프라인(retrieve_facts → select_relevant_facts → generate)이 여전히 분기·병렬 없는 선형
체인이라 오케스트레이션 프레임워크 도입 기준을 못 채운다 — LangGraph 든 PydanticAI 든, 그 결정을
미루는 자리가 `ports/resume.py`(경계는 고정, 구현만 갈아끼운다)다.
"""

from auto_apply.adapters.resume._assemble import assemble_resume, used_fact_ids
from auto_apply.ai.prompts import build_resume_prompt, reprompt_error_suffix
from auto_apply.ai.schemas import ResumeContentSchema
from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
)
from auto_apply.domain.errors import LLMSchemaViolation
from auto_apply.domain.resume_blocks import group_facts_for_resume, select_relevant_blocks
from auto_apply.domain.resume_matching import ground_check, select_relevant_facts
from auto_apply.ports.clock import IdGen
from auto_apply.ports.facts import FactSource
from auto_apply.ports.guide import GuideSource
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.profile import ProfileSource


class SimpleResumeGenerator:
    def __init__(
        self,
        llm: LLMClient,
        idgen: IdGen,
        facts: FactSource,
        profile: ProfileSource,
        guide: GuideSource,
        *,
        max_reprompts: int = 2,
        max_project_blocks: int = 3,
        max_career_blocks_per_entity: int = 4,
    ) -> None:
        self._llm = llm
        self._idgen = idgen
        self._facts = facts
        self._profile = profile
        self._guide = guide
        self._max_reprompts = max_reprompts
        self._max_project_blocks = max_project_blocks
        self._max_career_blocks_per_entity = max_career_blocks_per_entity

    async def generate(self, req: GenerateResumeRequest) -> ResumeDraft:
        facts = await self._facts.list_for_user(req.user_id)
        profile = await self._profile.get(req.user_id)
        guide = await self._guide.get(req.job.platform)
        job_text = f"{req.job.title}\n{req.job.description}"

        relevant = select_relevant_facts(facts, job_text)
        blocks = select_relevant_blocks(
            group_facts_for_resume(facts),
            job_text,
            max_projects=self._max_project_blocks,
            max_career_blocks_per_entity=self._max_career_blocks_per_entity,
        )

        content = await self._structured_with_reprompt(
            build_resume_prompt(req.job, relevant, blocks, guide=guide, feedback=req.feedback)
        )
        assembled = assemble_resume(profile, blocks, content)
        return ResumeDraft(
            resume_id=self._idgen.new_id("res"),
            content=assembled.model_dump(),
            used_fact_ids=used_fact_ids(content),
        )

    async def _structured_with_reprompt(self, prompt: str) -> ResumeContentSchema:
        # 원본 prompt 를 cache_prefix 로 고정해 재프롬프트 시도 전체가 같은 캐시 경계를
        # 공유하게 한다 — 매 시도 addition(빈 문자열 또는 오류 안내문)만 바뀐다. 캐시를
        # 태우는 구현(ClaudeCodeCliLLM 등)이라면 2·3번째 시도가 원본을 cache_read 로 읽는다
        # ([[claude-cli-prompt-cache-redesign]]).
        addition = ""
        last_error: LLMSchemaViolation | None = None
        for _ in range(self._max_reprompts + 1):
            try:
                return await self._llm.structured(
                    addition, ResumeContentSchema, cache_prefix=prompt
                )
            except LLMSchemaViolation as e:
                last_error = e
                addition = reprompt_error_suffix(str(e))
        assert last_error is not None  # for 루프가 최소 1회 돌아 last_error 가 반드시 세팅된다
        raise last_error


class SimpleResumeReviewer:
    """review 게이트. 길이 체크 다음으로, fact 근거 없는 서술을 `ground_check`로 잡는다(§2.3)."""

    def __init__(self, facts: FactSource, *, min_summary_len: int = 10) -> None:
        self._facts = facts
        self._min_summary_len = min_summary_len

    async def review(self, req: ReviewRequest) -> ReviewVerdict:
        summary = str(req.draft.content.get("summary", ""))
        issues: list[str] = []
        if len(summary) < self._min_summary_len:
            issues.append("summary 가 너무 짧다")

        facts = await self._facts.list_for_user(req.user_id)
        issues.extend(ground_check(req.draft, facts))

        score = 1.0 if not issues else max(0.0, 1.0 - 0.25 * len(issues))
        return ReviewVerdict(passed=not issues, score=score, issues=issues)
