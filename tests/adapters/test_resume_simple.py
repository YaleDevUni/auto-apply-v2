"""SimpleResumeGenerator/SimpleResumeReviewer — fact 기반 생성 + review 게이트 (§2.3)."""

import pytest

from auto_apply.adapters.clock.system import UuidIdGen
from auto_apply.adapters.facts.static import StaticFactSource
from auto_apply.adapters.guide.static import StaticGuideSource
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.profile.static import StaticProfileSource
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.contracts.dto import GenerateResumeRequest, JobRef, ResumeDraft, ReviewRequest
from auto_apply.contracts.fact import Fact
from auto_apply.contracts.profile import Profile
from auto_apply.domain.errors import LLMSchemaViolation
from auto_apply.ports.guide import GuideSource

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
FACT_BLOCK = Fact(
    id="exp-block-1",
    user_id="u1",
    kind="experience",
    content="Acme 에서 결제 API 를 설계했다.",
    keywords=["FastAPI", "결제"],
    entity="acme",
    entity_label="Acme(백엔드 인턴)",
    entity_period="2023.01 - 2023.12",
    block="payment-api",
    block_label="결제 API 개발",
    block_period="2023.01 - 2023.06",
)
PROFILE = Profile(user_id="u1", name="테스터")
VALID_PAYLOAD = {
    "summary": "FastAPI 경험을 살린 백엔드 엔지니어입니다",
    "highlights": [{"text": "FastAPI 결제 API 개발", "fact_ids": ["exp-1"]}],
}


def _profile_source() -> StaticProfileSource:
    return StaticProfileSource([PROFILE])


class _RecordingLLM(StubLLM):
    """StubLLM 을 감싸 마지막 structured() 호출의 prompt 를 기록한다 — guide/feedback 이

    실제로 프롬프트에 실리는지(SimpleResumeGenerator 배선) 검증하는 데 쓴다.
    """

    def __init__(self, payloads: list[dict[str, object]]) -> None:
        super().__init__(payloads=payloads)
        self.last_prompt: str = ""

    async def structured(self, prompt, schema, *, max_tokens=2048, cache_key=None):  # type: ignore[override]
        self.last_prompt = prompt
        return await super().structured(prompt, schema, max_tokens=max_tokens, cache_key=cache_key)


async def test_generate_grounds_used_fact_ids_from_llm_output():
    facts = StaticFactSource([FACT])
    gen = SimpleResumeGenerator(
        StubLLM(payloads=[VALID_PAYLOAD]),
        UuidIdGen(),
        facts,
        _profile_source(),
        StaticGuideSource(),
    )
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    assert draft.used_fact_ids == ["exp-1"]
    assert draft.content["summary"] == VALID_PAYLOAD["summary"]
    assert draft.content["name"] == "테스터"


async def test_generate_passes_guide_and_feedback_into_the_prompt():
    """가이드(영속, general REVISE)와 feedback(1회성, specific REVISE)이 모두 프롬프트에 실린다."""
    facts = StaticFactSource([FACT])
    llm = _RecordingLLM([VALID_PAYLOAD])
    guide: GuideSource = StaticGuideSource("항상 존댓말로 쓴다")
    gen = SimpleResumeGenerator(llm, UuidIdGen(), facts, _profile_source(), guide)
    await gen.generate(
        GenerateResumeRequest(
            application_id="a1", user_id="u1", job=JOB, feedback="자기소개를 더 짧게"
        )
    )
    assert "항상 존댓말로 쓴다" in llm.last_prompt
    assert "자기소개를 더 짧게" in llm.last_prompt


async def test_generate_assembles_block_bullets_into_career_section():
    """블록 제목·기간·회사명은 결정론 코드가 채우고, LLM 은 블록 안 불릿만 쓴다."""
    facts = StaticFactSource([FACT_BLOCK])
    payload = {
        "summary": "충분히 긴 요약 문장입니다",
        "blocks": [
            {
                "block_id": "acme:payment-api",
                "bullets": [{"text": "결제 API 를 설계했다", "fact_ids": ["exp-block-1"]}],
            }
        ],
    }
    gen = SimpleResumeGenerator(
        StubLLM(payloads=[payload]), UuidIdGen(), facts, _profile_source(), StaticGuideSource()
    )
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    career = draft.content["career"]
    assert career[0]["company"] == "Acme(백엔드 인턴)"
    assert career[0]["blocks"][0]["title"] == "결제 API 개발"
    assert career[0]["blocks"][0]["tech_stack"] == ["FastAPI", "결제"]
    assert career[0]["blocks"][0]["bullets"][0]["fact_ids"] == ["exp-block-1"]
    assert draft.used_fact_ids == ["exp-block-1"]


async def test_generate_reprompts_on_schema_violation_then_succeeds():
    """첫 응답이 스키마를 위반(창작 필드)해도 재프롬프트로 회복한다 (§5)."""
    facts = StaticFactSource([FACT])
    bad_payload = {**VALID_PAYLOAD, "made_up_field": "LLM이 창작한 필드"}
    llm = StubLLM(payloads=[bad_payload, VALID_PAYLOAD])
    gen = SimpleResumeGenerator(llm, UuidIdGen(), facts, _profile_source(), StaticGuideSource())
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    assert draft.used_fact_ids == ["exp-1"]


async def test_generate_raises_after_exhausting_reprompts():
    facts = StaticFactSource([FACT])
    bad_payload = {**VALID_PAYLOAD, "made_up_field": "x"}
    llm = StubLLM(payloads=[bad_payload, bad_payload, bad_payload])
    gen = SimpleResumeGenerator(
        llm, UuidIdGen(), facts, _profile_source(), StaticGuideSource(), max_reprompts=2
    )
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
