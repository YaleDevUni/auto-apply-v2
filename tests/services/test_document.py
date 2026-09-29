"""DocumentService — 생성·검토 루프 (v2 ResumeWorkflow 테스트를 Temporal 없이 옮김)."""

import pytest

from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    JobRef,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
)
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
