"""이력서 생성·검토 루프.

생성 → 검토(ground_check) → 재생성을 최대 `MAX_REVIEW_ROUNDS` 번 돈다. T0.2 에서 Temporal 을
걷어내며 `services/document.py` 로 옮긴다.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from auto_apply.contracts.activity_defs import generate_resume, review_resume
from auto_apply.contracts.dto import GenerateResumeRequest, ResumeDraft, ReviewRequest
from auto_apply.domain.errors import NON_RETRYABLE

MAX_REVIEW_ROUNDS = 3
# non_retryable_error_types 가 빠지면 LLMAuthRequired/LLMQuotaExceeded 처럼 재시도로 안 풀리는
# 실패도 maximum_attempts 만큼 반복하고서야 끝난다.
_AI_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    non_retryable_error_types=NON_RETRYABLE,
)


@workflow.defn
class ResumeWorkflow:
    def __init__(self) -> None:
        self._round = 0

    @workflow.run
    async def run(self, req: GenerateResumeRequest) -> ResumeDraft:
        issues: list[str] = []
        for round_no in range(1, MAX_REVIEW_ROUNDS + 1):
            self._round = round_no
            draft = await workflow.execute_activity(
                generate_resume,
                req,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_AI_RETRY,
            )
            verdict = await workflow.execute_activity(
                review_resume,
                ReviewRequest(draft=draft, job=req.job, user_id=req.user_id),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_AI_RETRY,
            )
            if verdict.passed:
                return draft
            issues = verdict.issues

        raise ApplicationError(
            f"검토 {MAX_REVIEW_ROUNDS}회 실패: {issues}",
            type="ResumeReviewExhausted",
            non_retryable=True,
        )

    @workflow.query
    def review_round(self) -> int:
        return self._round
