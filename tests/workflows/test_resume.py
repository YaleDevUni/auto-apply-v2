"""ResumeWorkflow 테스트 — 생성·검토 루프와 재시도 불가 LLM 실패 처리."""

import pytest
from temporalio.client import Client, WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auto_apply.activities.resume import ResumeActivities
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
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_AI
from auto_apply.workflows.resume import MAX_REVIEW_ROUNDS, ResumeWorkflow

pytestmark = pytest.mark.temporal

_JOB = JobRef(job_id="j1", platform="fixture", url="https://x", title="백엔드", company="테스트")
_DRAFT = ResumeDraft(resume_id="r1", content={}, used_fact_ids=[])


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
    def __init__(self, *, passed: bool) -> None:
        self._passed = passed

    async def review(self, req: ReviewRequest) -> ReviewVerdict:
        issues = [] if self._passed else ["근거 없는 서술"]
        return ReviewVerdict(passed=self._passed, score=1.0 if self._passed else 0.0, issues=issues)


async def _run(generator: _CountingGenerator, reviewer: _Reviewer) -> ResumeDraft:
    acts = ResumeActivities(generator, reviewer, StubPdfRenderer(InMemoryBlobStore()))
    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        client: Client = env.client
        async with Worker(
            client, task_queue=QUEUE_AI, workflows=[ResumeWorkflow], activities=acts.all()
        ):
            return await client.execute_workflow(
                ResumeWorkflow.run,
                GenerateResumeRequest(application_id="app_1", user_id="u1", job=_JOB),
                id="resume-test",
                task_queue=QUEUE_AI,
            )


async def test_returns_draft_when_review_passes():
    generator = _CountingGenerator()

    assert await _run(generator, _Reviewer(passed=True)) == _DRAFT
    assert generator.calls == 1


async def test_fails_after_max_review_rounds():
    generator = _CountingGenerator()

    with pytest.raises(WorkflowFailureError):
        await _run(generator, _Reviewer(passed=False))
    assert generator.calls == MAX_REVIEW_ROUNDS


async def test_non_retryable_llm_failure_is_not_retried():
    """LLMAuthRequired 는 NON_RETRYABLE 이라 activity 재시도 없이 바로 실패한다."""
    generator = _CountingGenerator(LLMAuthRequired("claude CLI 로그인 필요"))

    with pytest.raises(WorkflowFailureError):
        await _run(generator, _Reviewer(passed=True))
    assert generator.calls == 1
