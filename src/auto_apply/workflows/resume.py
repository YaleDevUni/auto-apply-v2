"""이력서 생성·검토 루프 (ARCHITECTURE.md §2.3).

큰 루프(생성 → 검토 → 재생성)는 Temporal 에 노출한다. UI 에서 3회 시도가 보여야 디버깅이 된다.
그래프 내부의 세부 노드는 activity 안(M3 LangGraph)으로 숨긴다.
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TypeVar

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from auto_apply.contracts.activity_defs import generate_resume, notify, review_resume
from auto_apply.contracts.dto import (
    GenerateResumeRequest,
    NotifyEvent,
    ResumeDraft,
    ReviewRequest,
    ReviewVerdict,
)
from auto_apply.domain.errors import NON_RETRYABLE
from auto_apply.temporal_config import QUEUE_DEFAULT

MAX_REVIEW_ROUNDS = 3
# non_retryable_error_types 를 넘겨야 아래 docstring 이 말하는 "1회만 시도" 가 실제로 맞다 —
# 이게 빠지면 LLMAuthRequired/LLMQuotaExceeded 도 maximum_attempts 만큼 반복하고서야 끝난다.
_AI_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    non_retryable_error_types=NON_RETRYABLE,
)

# claude CLI 가 재시도로 저절로 안 풀리는 실패를 이렇게 분류해서 던진다
# (domain/errors.py LLMAuthRequired/LLMQuotaExceeded, adapters/llm/claude_code_cli.py).
# 사람이 즉시 알아야 개입할 수 있어 텔레그램으로 알린다 — 그 외 실패(LLMExecutionError 등)는
# 대부분 일시적이라 activity 재시도로 회복을 시도한 뒤 그래도 안 되면 그냥 워크플로우가 실패한다.
# LLMQuotaExceeded 메시지의 "{hours}" 는 `_run_llm_activity`가 req.approval_timeout_hours 로
# 채운다 — 다른 메시지는 자리표시자가 없어 `.format()`이 그냥 원문을 돌려준다.
_LLM_FAILURE_MESSAGES = {
    "LLMAuthRequired": (
        "claude CLI 로그인이 풀렸다 — 이 머신에서 `claude login` 을 다시 해야 재개된다."
    ),
    "LLMQuotaExceeded": (
        "claude 구독 사용량 한도를 넘었다 — 한도가 풀리면 텔레그램에서 '한도 풀렸으니"
        " 재개해줘'처럼 말하면 멈춘 지점부터 이어서 진행한다({hours}시간 안에 재개 신호가"
        " 없으면 포기하고 사람에게 넘긴다)."
    ),
}

# 사람이 텔레그램에서 재개(retry_now) 신호를 주면 그 자리에서 이어가는 실패 — LLMQuotaExceeded
# 만 해당한다. LLMAuthRequired 는 재개 신호만으로는 안 풀린다(로그인 자체를 다시 해야 한다)는
# 판단에 따라 지금처럼 즉시 실패시킨다 (§11.2c, 사용자 확정으로 범위를 한도초과만으로 좁힘).
_PAUSABLE_LLM_FAILURES = frozenset({"LLMQuotaExceeded"})

_T = TypeVar("_T")


@workflow.defn
class ResumeWorkflow:
    def __init__(self) -> None:
        self._round = 0
        self._paused = False
        self._retry_requested = False

    @workflow.run
    async def run(self, req: GenerateResumeRequest) -> ResumeDraft:
        issues: list[str] = []
        for round_no in range(1, MAX_REVIEW_ROUNDS + 1):
            self._round = round_no

            def _generate_call() -> Awaitable[ResumeDraft]:
                return workflow.execute_activity(
                    generate_resume,
                    req,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_AI_RETRY,
                )

            draft = await self._run_llm_activity(req, _generate_call)

            # draft 를 기본 인자로 묶어둔다 — 클로저로 그냥 참조하면 다음 순회가 draft 를
            # 다시 assign 했을 때 이 함수가 새 값을 보게 되는 버그(ruff B023)가 생긴다.
            def _review_call(draft: ResumeDraft = draft) -> Awaitable[ReviewVerdict]:
                return workflow.execute_activity(
                    review_resume,
                    ReviewRequest(draft=draft, job=req.job, user_id=req.user_id),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_AI_RETRY,
                )

            verdict = await self._run_llm_activity(req, _review_call)
            if verdict.passed:
                return draft
            issues = verdict.issues

        raise ApplicationError(
            f"검토 {MAX_REVIEW_ROUNDS}회 실패: {issues}",
            type="ResumeReviewExhausted",
            non_retryable=True,
        )

    async def _run_llm_activity(
        self, req: GenerateResumeRequest, make_call: Callable[[], Awaitable[_T]]
    ) -> _T:
        """generate_resume/review_resume 공통 실행부.

        `make_call`은 매번 새 activity 실행을 만드는 factory 다(코루틴 하나는 한 번만 await
        할 수 있어, 재시도 때마다 다시 만들어야 한다). claude CLI 가 재시도로 안 풀리는 실패
        (로그인 풀림·사용량 한도)로 죽으면 재던지기 전에 사람에게 텔레그램으로 알린다. 이 중
        LLMQuotaExceeded 는 알림 뒤 바로 재던지지 않고 `_wait_for_retry`로 사람이 텔레그램에서
        재개 신호(`retry_now` signal, 텔레그램 `resume_llm_generation` 도구)를 줄 때까지
        durable 하게 멈춰 있다가 같은 호출을 다시 시도한다(§11.2c) — CLAUDE.md "승인/예약
        대기는 wait_condition" 패턴을 그대로 재사용했다. `req.approval_timeout_hours` 안에
        재개 신호가 없으면 포기하고 그대로 재던진다 — 이 경우 지금까지와 동일하게 NEEDS_HUMAN
        으로 끝난다. LLMAuthRequired 는 `_PAUSABLE_LLM_FAILURES`에 없어 알림만 보내고 바로
        재던진다(로그인은 재개 신호만으로 안 풀린다).
        """
        while True:
            try:
                return await make_call()
            except ActivityError as e:
                cause = e.cause
                failure_type = cause.type if isinstance(cause, ApplicationError) else None
                message = _LLM_FAILURE_MESSAGES.get(failure_type or "")
                if message:
                    await workflow.execute_activity(
                        notify,
                        NotifyEvent(
                            kind=failure_type,
                            application_id=req.application_id,
                            message=message.format(hours=req.approval_timeout_hours),
                        ),
                        start_to_close_timeout=timedelta(minutes=2),
                        task_queue=QUEUE_DEFAULT,
                    )
                if failure_type not in _PAUSABLE_LLM_FAILURES:
                    raise
                if not await self._wait_for_retry(req):
                    raise

    async def _wait_for_retry(self, req: GenerateResumeRequest) -> bool:
        """재개 signal 을 기다린다. 타임아웃 안에 안 오면 포기(False)를 돌려준다.

        `paused` query 가 True 를 돌려주는 구간이 정확히 이 대기 구간이다 — 대기를 걸기
        직전에 켜고 빠져나가기 직전에 끈다(테스트가 이 query 로 "지금 막 멈췄다"를 확인한
        뒤에만 signal 을 보내면, 그 사이 새 시도가 시작되며 플래그가 리셋되는 경합이 없다).
        """
        self._paused = True
        self._retry_requested = False
        try:
            await workflow.wait_condition(
                lambda: self._retry_requested,
                timeout=timedelta(hours=req.approval_timeout_hours),
            )
            return True
        except TimeoutError:
            return False
        finally:
            self._paused = False

    @workflow.signal
    def retry_now(self) -> None:
        """텔레그램 `resume_llm_generation` 도구가 보낸다.

        멈춰 있지 않을 때 와도(예: 중복 신호) 다음 대기 진입 시 리셋되므로 무해하다 — 텔레그램
        버튼/도구 호출이 두 번 일어날 수 있다는 CLAUDE.md 관례와 같은 이유로 멱등하게 둔다.
        """
        self._retry_requested = True

    @workflow.query
    def review_round(self) -> int:
        return self._round

    @workflow.query
    def paused(self) -> bool:
        """claude CLI 한도초과로 재개 신호를 기다리는 중인지 (§11.2c)."""
        return self._paused
