"""이력서 생성·검토 루프 (ARCHITECTURE.md §2.3).

큰 루프(생성 → 검토 → 재생성)는 Temporal 에 노출한다. UI 에서 3회 시도가 보여야 디버깅이 된다.
그래프 내부의 세부 노드는 activity 안(M3 LangGraph)으로 숨긴다.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from auto_apply.contracts.activity_defs import generate_resume, review_resume
from auto_apply.contracts.dto import GenerateResumeRequest, ResumeDraft, ReviewRequest

MAX_REVIEW_ROUNDS = 3
_AI_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2))


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
