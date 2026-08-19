"""ResumeWorkflow 테스트 — LLM 실패 분류·알림 (ARCHITECTURE.md §2.3, §11.2c).

claude CLI 가 재시도로 안 풀리는 실패(LLMAuthRequired/LLMQuotaExceeded)로 죽으면 그대로
워크플로우가 실패하기 전에 텔레그램(여기선 `notify` activity 스텁)으로 사람에게 알려야 한다.
generate_resume/review_resume 은 `ai` 큐, notify 는 `default` 큐에 있어 워커 두 개로 나눈다
(§1 큐 분리 규칙을 이 테스트도 그대로 지킨다).
"""

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auto_apply.activities.resume import ResumeActivities
from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    JobRef,
    NotifyEvent,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
)
from auto_apply.domain.errors import LLMAuthRequired, LLMExecutionError, LLMQuotaExceeded
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_AI, QUEUE_DEFAULT
from auto_apply.workflows.resume import ResumeWorkflow

pytestmark = pytest.mark.integration

_JOB = JobRef(job_id="j1", platform="fixture", url="https://x", title="백엔드", company="테스트")


class _RaisingGenerator:
    """실제로는 ClaudeCodeCliLLM 이 던지는 걸 흉내낸다 — generator 는 그걸 그대로 전파한다."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def generate(self, req: GenerateResumeRequest) -> ResumeDraft:
        raise self._error


class _NeverCalledReviewer:
    async def review(self, req: ReviewRequest) -> ReviewVerdict:
        raise AssertionError("generate_resume 이 실패했으면 review_resume 은 안 불려야 한다")


@activity.defn(name="notify")
async def _capturing_notify(event: NotifyEvent) -> None:
    _captured_events.append(event)


_captured_events: list[NotifyEvent] = []


async def _run_env(error: Exception) -> WorkflowFailureError:
    _captured_events.clear()
    store = InMemoryBlobStore()
    resume_acts = ResumeActivities(
        _RaisingGenerator(error), _NeverCalledReviewer(), StubPdfRenderer(store)
    )

    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        client: Client = env.client
        async with (
            Worker(
                client,
                task_queue=QUEUE_AI,
                workflows=[ResumeWorkflow],
                activities=resume_acts.all(),
            ),
            Worker(client, task_queue=QUEUE_DEFAULT, activities=[_capturing_notify]),
        ):
            with pytest.raises(WorkflowFailureError) as exc_info:
                await client.execute_workflow(
                    ResumeWorkflow.run,
                    GenerateResumeRequest(application_id="app_1", user_id="u1", job=_JOB),
                    id="resume-test-1",
                    task_queue=QUEUE_AI,
                )
    return exc_info.value


async def test_llm_auth_required_notifies_before_failing():
    await _run_env(LLMAuthRequired("claude CLI 로그인 필요: Not logged in · Please run /login"))

    assert len(_captured_events) == 1
    event = _captured_events[0]
    assert event.kind == "LLMAuthRequired"
    assert event.application_id == "app_1"
    assert "로그인" in event.message


async def test_llm_quota_exceeded_notifies_before_failing():
    await _run_env(LLMQuotaExceeded("claude CLI 사용량 한도 초과: budget_exhausted"))

    assert len(_captured_events) == 1
    event = _captured_events[0]
    assert event.kind == "LLMQuotaExceeded"
    assert event.application_id == "app_1"
    assert "한도" in event.message


async def test_generic_llm_failure_does_not_notify():
    """분류 안 된 실패(타임아웃 등)는 activity 재시도에 맡긴다 — 알림 대상이 아니다."""
    await _run_env(LLMExecutionError("claude CLI 타임아웃(120.0s)"))

    assert _captured_events == []
