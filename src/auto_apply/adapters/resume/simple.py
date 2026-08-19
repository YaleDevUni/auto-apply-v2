"""plain 함수 기반 이력서 생성/검토 (ARCHITECTURE.md §2.3, §9.2).

파이프라인(retrieve_facts → select_relevant_facts → generate)이 여전히 분기·병렬 없는 선형
체인이라 오케스트레이션 프레임워크 도입 기준을 못 채운다 — LangGraph 든 PydanticAI 든, 그 결정을
미루는 자리가 `ports/resume.py`(경계는 고정, 구현만 갈아끼운다)다.
"""

from auto_apply.ai.prompts import build_resume_prompt, reprompt_with_error
from auto_apply.ai.schemas import ResumeContentSchema
from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
)
from auto_apply.domain.errors import LLMSchemaViolation
from auto_apply.domain.resume_matching import ground_check, select_relevant_facts
from auto_apply.ports.clock import IdGen
from auto_apply.ports.facts import FactSource
from auto_apply.ports.llm import LLMClient


class SimpleResumeGenerator:
    def __init__(
        self,
        llm: LLMClient,
        idgen: IdGen,
        facts: FactSource,
        *,
        max_reprompts: int = 2,
    ) -> None:
        self._llm = llm
        self._idgen = idgen
        self._facts = facts
        self._max_reprompts = max_reprompts

    async def generate(self, req: GenerateResumeRequest) -> ResumeDraft:
        facts = await self._facts.list_for_user(req.user_id)
        job_text = f"{req.job.title}\n{req.job.description}"
        relevant = select_relevant_facts(facts, job_text)

        content = await self._structured_with_reprompt(build_resume_prompt(req.job, relevant))
        used_ids = sorted({fid for h in content.highlights for fid in h.fact_ids})
        return ResumeDraft(
            resume_id=self._idgen.new_id("res"),
            content=content.model_dump(),
            used_fact_ids=used_ids,
        )

    async def _structured_with_reprompt(self, prompt: str) -> ResumeContentSchema:
        attempt_prompt = prompt
        last_error: LLMSchemaViolation | None = None
        for _ in range(self._max_reprompts + 1):
            try:
                return await self._llm.structured(attempt_prompt, ResumeContentSchema)
            except LLMSchemaViolation as e:
                last_error = e
                attempt_prompt = reprompt_with_error(prompt, str(e))
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
