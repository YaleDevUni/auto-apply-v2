"""ResumeWorkflow 테스트 — LLM 실패 분류·알림·한도초과 pause-and-resume (ARCHITECTURE.md §2.3,
§11.2c).

claude CLI 가 재시도로 안 풀리는 실패(LLMAuthRequired/LLMQuotaExceeded)로 죽으면 그대로
워크플로우가 실패하기 전에 텔레그램(여기선 `notify` activity 스텁)으로 사람에게 알려야 한다.
generate_resume/review_resume 은 `ai` 큐, notify 는 `default` 큐에 있어 워커 두 개로 나눈다
(§1 큐 분리 규칙을 이 테스트도 그대로 지킨다). LLMQuotaExceeded 만 추가로 재개(retry_now)
signal 을 받을 때까지 멈춰 있다가 이어간다 — 나머지(LLMAuthRequired/분류 안 된 실패)는 지금처럼
즉시 끝난다.
"""

import asyncio

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

pytestmark = pytest.mark.temporal

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
    """재개 신호를 아무도 안 보내면, `approval_timeout_hours`(기본 72h, 시간 건너뛰기로

    즉시 검증) 동안 `paused`로 멈춰 있다가 포기하고 실패로 끝난다 — 알림은 멈추기 직전
    1번만 나간다(§11.2c pause-and-resume, 재개 성공 경로는
    test_llm_quota_exceeded_pauses_then_resumes_on_retry_now_signal).
    """
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


class _FlakyGenerator:
    """첫 호출은 한도초과로 실패, 재개 신호 뒤 두 번째 호출은 성공 — pause-and-resume 검증용."""

    def __init__(self, error: Exception, draft: ResumeDraft) -> None:
        self._error = error
        self._draft = draft
        self.calls = 0

    async def generate(self, req: GenerateResumeRequest) -> ResumeDraft:
        self.calls += 1
        if self.calls == 1:
            raise self._error
        return self._draft


class _AlwaysPassingReviewer:
    async def review(self, req: ReviewRequest) -> ReviewVerdict:
        return ReviewVerdict(passed=True, score=1.0)


async def test_llm_quota_exceeded_pauses_then_resumes_on_retry_now_signal():
    """알림 뒤 바로 실패하지 않고 `paused` query 가 True 인 동안 멈춰 있다가, `retry_now`

    signal 을 받으면 실패했던 generate_resume 호출을 다시 시도해 이어간다(§11.2c).
    """
    _captured_events.clear()
    store = InMemoryBlobStore()
    draft = ResumeDraft(resume_id="r1", content={}, used_fact_ids=[])
    generator = _FlakyGenerator(
        LLMQuotaExceeded("claude CLI 사용량 한도 초과: budget_exhausted"), draft
    )
    resume_acts = ResumeActivities(generator, _AlwaysPassingReviewer(), StubPdfRenderer(store))

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
            handle = await client.start_workflow(
                ResumeWorkflow.run,
                GenerateResumeRequest(application_id="app_1", user_id="u1", job=_JOB),
                id="resume-test-2",
                task_queue=QUEUE_AI,
            )
            for _ in range(200):
                if await handle.query(ResumeWorkflow.paused):
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("워크플로우가 멈추지 않았다")

            await handle.signal(ResumeWorkflow.retry_now)
            result = await handle.result()

    assert result == draft
    assert generator.calls == 2
    assert len(_captured_events) == 1
    assert _captured_events[0].kind == "LLMQuotaExceeded"
