"""이력서 생성·검토 루프 (ARCHITECTURE.md §2.3).

큰 루프(생성 → 검토 → 재생성)는 Temporal 에 노출한다. UI 에서 3회 시도가 보여야 디버깅이 된다.
그래프 내부의 세부 노드는 activity 안(M3 LangGraph)으로 숨긴다.
"""

from collections.abc import Awaitable
from datetime import timedelta
from typing import TypeVar

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from auto_apply.contracts.activity_defs import generate_resume, notify, review_resume
from auto_apply.contracts.dto import GenerateResumeRequest, NotifyEvent, ResumeDraft, ReviewRequest
from auto_apply.temporal_config import QUEUE_DEFAULT

MAX_REVIEW_ROUNDS = 3
_AI_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2))

# claude CLI 가 재시도로 저절로 안 풀리는 실패를 이렇게 분류해서 던진다
# (domain/errors.py LLMAuthRequired/LLMQuotaExceeded, adapters/llm/claude_code_cli.py).
# 사람이 즉시 알아야 개입할 수 있어 텔레그램으로 알린다 — 그 외 실패(LLMExecutionError 등)는
# 대부분 일시적이라 activity 재시도로 회복을 시도한 뒤 그래도 안 되면 그냥 워크플로우가 실패한다.
_LLM_FAILURE_MESSAGES = {
    "LLMAuthRequired": (
        "claude CLI 로그인이 풀렸다 — 이 머신에서 `claude login` 을 다시 해야 재개된다."
    ),
    "LLMQuotaExceeded": ("claude 구독 사용량 한도를 넘었다 — 리셋될 때까지 이력서 생성이 막힌다."),
}

_T = TypeVar("_T")


@workflow.defn
class ResumeWorkflow:
    def __init__(self) -> None:
        self._round = 0

    @workflow.run
    async def run(self, req: GenerateResumeRequest) -> ResumeDraft:
        issues: list[str] = []
        for round_no in range(1, MAX_REVIEW_ROUNDS + 1):
            self._round = round_no
            draft = await self._run_llm_activity(
                req,
                workflow.execute_activity(
                    generate_resume,
                    req,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_AI_RETRY,
                ),
            )
            verdict = await self._run_llm_activity(
                req,
                workflow.execute_activity(
                    review_resume,
                    ReviewRequest(draft=draft, job=req.job, user_id=req.user_id),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_AI_RETRY,
                ),
            )
            if verdict.passed:
                return draft
            issues = verdict.issues

        raise ApplicationError(
            f"검토 {MAX_REVIEW_ROUNDS}회 실패: {issues}",
            type="ResumeReviewExhausted",
            non_retryable=True,
        )

    async def _run_llm_activity(self, req: GenerateResumeRequest, call: Awaitable[_T]) -> _T:
        """generate_resume/review_resume 공통 실행부.

        `call`은 아직 실행되지 않은 activity 호출(코루틴)이다 — 만들어두기만 하고 여기서 await 한다.
        claude CLI 가 재시도로 안 풀리는 실패(로그인 풀림·사용량 한도)로 죽으면 그대로 재던지기
        전에 사람에게 텔레그램으로 알린다. 두 실패 모두 NON_RETRYABLE 이라 Temporal 이 1회만
        시도하므로 여기서 알림도 정확히 1번만 나간다 — 재시도 폭주로 알림이 반복 발사될 걱정이
        없다(§CLAUDE.md "Temporal 관련 주의").
        """
        try:
            return await call
        except ActivityError as e:
            cause = e.cause
            failure_type = cause.type if isinstance(cause, ApplicationError) else None
            message = _LLM_FAILURE_MESSAGES.get(failure_type or "")
            if message:
                await workflow.execute_activity(
                    notify,
                    NotifyEvent(
                        kind=failure_type, application_id=req.application_id, message=message
                    ),
                    start_to_close_timeout=timedelta(minutes=2),
                    task_queue=QUEUE_DEFAULT,
                )
            raise

    @workflow.query
    def review_round(self) -> int:
        return self._round
