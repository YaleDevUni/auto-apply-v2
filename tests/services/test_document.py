"""DocumentService — 생성·검토 루프 (v2 ResumeWorkflow 테스트를 Temporal 없이 옮김)."""

import pytest

from auto_apply.adapters.clock.system import UuidIdGen
from auto_apply.adapters.facts.repository import RepositoryFactSource
from auto_apply.adapters.guide.static import StaticGuideSource
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.profile.repository import RepositoryProfileSource
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    JobRef,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
)
from auto_apply.contracts.experience import Experience, ExperienceFact, ExperienceSection
from auto_apply.contracts.profile import Profile
from auto_apply.domain.enums import ExperienceKind
from auto_apply.domain.errors import LLMAuthRequired
from auto_apply.services.document import MAX_REVIEW_ROUNDS, DocumentService, ResumeReviewExhausted

_JOB = JobRef(job_id="j1", platform="fixture", url="https://x", title="백엔드", company="테스트")
_DRAFT = ResumeDraft(resume_id="r1", content={}, used_fact_ids=[])
_REQ = GenerateResumeRequest(application_id="app_1", user_id="u1", job=_JOB)


class _CountingGenerator:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls = 0

    async def generate(self, req: GenerateResumeRequest) -> ResumeDraft:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return _DRAFT


class _Reviewer:
    """`pass_on` 번째 검토부터 통과시킨다 (None 이면 끝까지 실패)."""

    def __init__(self, *, pass_on: int | None) -> None:
        self._pass_on = pass_on
        self.calls = 0

    async def review(self, req: ReviewRequest) -> ReviewVerdict:
        self.calls += 1
        passed = self._pass_on is not None and self.calls >= self._pass_on
        return ReviewVerdict(
            passed=passed, score=1.0 if passed else 0.0, issues=[] if passed else ["근거 없음"]
        )


def _service(generator: _CountingGenerator, reviewer: _Reviewer) -> DocumentService:
    return DocumentService(generator, reviewer, StubPdfRenderer(InMemoryBlobStore()))


async def test_returns_draft_when_review_passes():
    generator = _CountingGenerator()

    assert await _service(generator, _Reviewer(pass_on=1)).generate_resume(_REQ) == _DRAFT
    assert generator.calls == 1


async def test_regenerates_until_review_passes():
    generator = _CountingGenerator()

    assert await _service(generator, _Reviewer(pass_on=2)).generate_resume(_REQ) == _DRAFT
    assert generator.calls == 2


async def test_fails_after_max_review_rounds():
    generator = _CountingGenerator()

    with pytest.raises(ResumeReviewExhausted) as exc:
        await _service(generator, _Reviewer(pass_on=None)).generate_resume(_REQ)
    assert generator.calls == MAX_REVIEW_ROUNDS
    assert exc.value.issues == ["근거 없음"]


async def test_llm_failure_propagates_without_retry():
    """재시도는 JobRunner 몫(§A9) — 서비스는 LLM 실패를 삼키거나 반복하지 않는다."""
    generator = _CountingGenerator(LLMAuthRequired("claude CLI 로그인 필요"))

    with pytest.raises(LLMAuthRequired):
        await _service(generator, _Reviewer(pass_on=1)).generate_resume(_REQ)
    assert generator.calls == 1


async def test_render_pdf_delegates_to_renderer():
    rendered = await _service(_CountingGenerator(), _Reviewer(pass_on=1)).render_pdf(_DRAFT)
    assert rendered.bytes_written > 0


# ── 저장소 경유 (T1.1): 프로필·경험을 DB 에서 읽어도 생성·검토가 그대로 돈다 ─────────────


async def test_generates_from_profile_and_experiences_in_repository(uow_factory):
    async with uow_factory() as uow:
        await uow.profiles.save(Profile(user_id="u1", name="홍길동", skills=["Python"]))
        await uow.experiences.save(
            Experience(
                id="acme",
                user_id="u1",
                kind=ExperienceKind.COMPANY,
                name="Acme",
                role="백엔드",
                period="2023.01 - 2023.12",
                sections=[
                    ExperienceSection(
                        key="pay",
                        title="결제 API",
                        facts=[
                            ExperienceFact(id="f-pay", text="결제 API 설계", skills=["FastAPI"])
                        ],
                    )
                ],
            )
        )
        await uow.commit()
    facts = RepositoryFactSource(uow_factory)
    payload = {
        "summary": "결제 API 를 설계한 백엔드 엔지니어입니다",
        "highlights": [{"text": "결제 API 설계", "fact_ids": ["f-pay"]}],
        "blocks": [
            {"block_id": "acme:pay", "bullets": [{"text": "결제 API 설계", "fact_ids": ["f-pay"]}]}
        ],
    }
    generator = SimpleResumeGenerator(
        StubLLM(payloads=[payload]),
        UuidIdGen(),
        facts,
        RepositoryProfileSource(uow_factory),
        StaticGuideSource(),
    )
    service = DocumentService(
        generator, SimpleResumeReviewer(facts), StubPdfRenderer(InMemoryBlobStore())
    )

    draft = await service.generate_resume(_REQ)

    assert draft.used_fact_ids == ["f-pay"]
    assert draft.content["name"] == "홍길동"
    [career] = draft.content["career"]
    assert career["company"] == "Acme(백엔드)"
    assert career["blocks"][0]["title"] == "결제 API"


async def test_hallucinated_fact_id_still_fails_review_through_repository(uow_factory):
    """ground_check 는 저장소의 fact 로 판정한다 — 없는 fact_id 인용은 여전히 거부 (절대 규칙 4)."""
    async with uow_factory() as uow:
        await uow.profiles.save(Profile(user_id="u1", name="홍길동"))
        await uow.commit()
    facts = RepositoryFactSource(uow_factory)
    payload = {
        "summary": "없는 경력을 쓴 요약 문장입니다",
        "highlights": [{"text": "지어낸 경력", "fact_ids": ["nope"]}],
    }
    generator = SimpleResumeGenerator(
        StubLLM(payloads=[payload] * MAX_REVIEW_ROUNDS),
        UuidIdGen(),
        facts,
        RepositoryProfileSource(uow_factory),
        StaticGuideSource(),
    )
    service = DocumentService(
        generator, SimpleResumeReviewer(facts), StubPdfRenderer(InMemoryBlobStore())
    )

    with pytest.raises(ResumeReviewExhausted):
        await service.generate_resume(_REQ)
