"""SimpleResumeGenerator/SimpleResumeReviewer — fact 기반 생성 + review 게이트 (§2.3)."""

import pytest

from auto_apply.adapters.clock.system import UuidIdGen
from auto_apply.adapters.facts.static import StaticFactSource
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.contracts.dto import GenerateResumeRequest, JobRef, ResumeDraft, ReviewRequest
from auto_apply.contracts.fact import Fact
from auto_apply.domain.errors import LLMSchemaViolation

JOB = JobRef(
    job_id="j1",
    platform="fixture",
    url="https://fixture.local/1",
    title="백엔드 엔지니어",
    company="Acme",
    description="FastAPI 경험 우대",
)
FACT = Fact(
    id="exp-1", user_id="u1", kind="experience", content="FastAPI 3년", keywords=["FastAPI"]
)
VALID_PAYLOAD = {
    "summary": "FastAPI 경험을 살린 백엔드 엔지니어입니다",
    "highlights": [{"text": "FastAPI 결제 API 개발", "fact_ids": ["exp-1"]}],
}


async def test_generate_grounds_used_fact_ids_from_llm_output():
    facts = StaticFactSource([FACT])
    gen = SimpleResumeGenerator(StubLLM(payloads=[VALID_PAYLOAD]), UuidIdGen(), facts)
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    assert draft.used_fact_ids == ["exp-1"]
    assert draft.content["summary"] == VALID_PAYLOAD["summary"]


async def test_generate_reprompts_on_schema_violation_then_succeeds():
    """첫 응답이 스키마를 위반(창작 필드)해도 재프롬프트로 회복한다 (§5)."""
    facts = StaticFactSource([FACT])
    bad_payload = {**VALID_PAYLOAD, "made_up_field": "LLM이 창작한 필드"}
    llm = StubLLM(payloads=[bad_payload, VALID_PAYLOAD])
    gen = SimpleResumeGenerator(llm, UuidIdGen(), facts)
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    assert draft.used_fact_ids == ["exp-1"]


async def test_generate_raises_after_exhausting_reprompts():
    facts = StaticFactSource([FACT])
    bad_payload = {**VALID_PAYLOAD, "made_up_field": "x"}
    llm = StubLLM(payloads=[bad_payload, bad_payload, bad_payload])
    gen = SimpleResumeGenerator(llm, UuidIdGen(), facts, max_reprompts=2)
    with pytest.raises(LLMSchemaViolation):
        await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))


async def test_review_fails_on_hallucinated_fact_id():
    """review 게이트 — 존재하지 않는 fact_id 를 인용한 초안은 통과하지 못한다."""
    facts = StaticFactSource([FACT])
    reviewer = SimpleResumeReviewer(facts)
    draft = ResumeDraft(
        resume_id="r1",
        content={
            "summary": "충분히 긴 요약 문장입니다",
            "highlights": [{"text": "지어낸 경력", "fact_ids": ["fake-id"]}],
        },
        used_fact_ids=["fake-id"],
    )
    verdict = await reviewer.review(ReviewRequest(draft=draft, job=JOB, user_id="u1"))
    assert verdict.passed is False
    assert any("존재하지 않는 fact_id" in issue for issue in verdict.issues)


async def test_review_passes_when_grounded():
    facts = StaticFactSource([FACT])
    reviewer = SimpleResumeReviewer(facts)
    draft = ResumeDraft(resume_id="r1", content=VALID_PAYLOAD, used_fact_ids=["exp-1"])
    verdict = await reviewer.review(ReviewRequest(draft=draft, job=JOB, user_id="u1"))
    assert verdict.passed is True
    assert verdict.issues == []
