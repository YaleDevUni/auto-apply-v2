"""지원 1건의 전 생애 (ARCHITECTURE.md §2.2).

이 파일의 규칙:
  - contracts / domain 만 import (§11.3)
  - 시각은 workflow.now(), 대기는 workflow.wait_condition — 절대 datetime.now()/asyncio.sleep 금지
  - 승인/예약 대기는 sleep 이 아니라 wait_condition(timeout=) 이어야 reschedule/cancel 이 먹는다
"""

from datetime import datetime, timedelta
from functools import partial

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ChildWorkflowError

from auto_apply.contracts.activity_defs import (
    collect_job,
    evaluate_eligibility,
    load_active_recipe,
    notify,
    persist_state,
    render_pdf,
    request_approval,
)
from auto_apply.contracts.dto import (
    ApplicationResult,
    ApproveSignal,
    Decision,
    DecisionRequest,
    GenerateResumeRequest,
    JobRef,
    NotifyEvent,
    PersistState,
    RejectSignal,
    RescheduleSignal,
    StartApplication,
    StateView,
)
from auto_apply.domain.enums import ApplicationState, DecisionKind
from auto_apply.workflows import _execution
from auto_apply.workflows.resume import ResumeWorkflow

QUEUE_AI = "ai"

_QUICK = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1))
_PERSIST = RetryPolicy(maximum_attempts=10, initial_interval=timedelta(seconds=1))


@workflow.defn
class ApplicationWorkflow:
    def __init__(self) -> None:
        self._state: ApplicationState = ApplicationState.COLLECTING
        self._decision: Decision | None = None
        self._decision_nonce: str | None = None
        self._scheduled_at: datetime | None = None
        self._cancelled = False
        self._attempts = 0

    # ─────────────────────────── run ───────────────────────────
    @workflow.run
    async def run(self, cmd: StartApplication) -> ApplicationResult:
        job = await workflow.execute_activity(
            collect_job,
            cmd.job_url,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK,
        )

        await self._persist(cmd, ApplicationState.EVALUATING)
        verdict = await workflow.execute_activity(
            evaluate_eligibility,
            job,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK,
        )
        if not verdict.eligible:
            return await self._finish(cmd, ApplicationState.REJECTED, verdict.reason)

        # ── 이력서 (child workflow: UI 에서 따로 추적·재실행 가능) ──
        await self._persist(cmd, ApplicationState.GENERATING_RESUME)
        try:
            draft = await workflow.execute_child_workflow(
                ResumeWorkflow.run,
                GenerateResumeRequest(
                    application_id=cmd.application_id, user_id=cmd.user_id, job=job
                ),
                id=f"resume-{cmd.application_id}-1",
                task_queue=QUEUE_AI,
            )
        except ChildWorkflowError as e:
            return await self._finish(cmd, ApplicationState.NEEDS_HUMAN, f"이력서 실패: {e.cause}")

        await self._persist(cmd, ApplicationState.RENDERING_PDF)
        pdf = await workflow.execute_activity(
            render_pdf,
            draft,
            start_to_close_timeout=timedelta(minutes=5),
            task_queue=QUEUE_AI,
            retry_policy=_QUICK,
        )

        # ── Human-in-the-loop ──
        decision = await self._await_decision(cmd, job, pdf.blob_key)
        if decision is None:
            return await self._finish(cmd, ApplicationState.EXPIRED, "승인 대기 시간 초과")
        if decision.kind is DecisionKind.REJECT:
            return await self._finish(cmd, ApplicationState.REJECTED, decision.reason)

        # ── Durable timer ──
        self._scheduled_at = decision.scheduled_at or workflow.now()
        if not await self._wait_until_scheduled(cmd):
            return await self._finish(cmd, ApplicationState.CANCELLED, "사용자 취소")

        # ── 실행 ──
        return await self._execute(cmd, job, pdf.blob_key)

    # ─────────────────────── 단계별 헬퍼 ───────────────────────
    async def _await_decision(
        self, cmd: StartApplication, job: JobRef, pdf_key: str
    ) -> Decision | None:
        await self._persist(cmd, ApplicationState.AWAITING_APPROVAL)
        ticket = await workflow.execute_activity(
            request_approval,
            DecisionRequest(
                application_id=cmd.application_id,
                workflow_id=workflow.info().workflow_id,
                title=f"{job.company} / {job.title} 지원 승인",
                summary=job.url,
                artifact_url=pdf_key,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK,
        )
        # nonce 검증은 여기(워크플로우)에서만 한다 — Notifier 어댑터 메모리에 두면 발급 프로세스
        # (worker)와 검증 프로세스(webhook/listener)가 갈라질 때 항상 실패한다. 실제로 라이브
        # 스모크테스트에서 그렇게 터졌다. 워크플로우는 Temporal 이 프로세스 경계와 무관하게
        # 들고 있는 유일한 상태라 여기가 맞는 자리다.
        self._decision_nonce = ticket.nonce
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None,
                timeout=timedelta(hours=cmd.approval_timeout_hours),
            )
        except TimeoutError:
            return None
        return self._decision

    async def _wait_until_scheduled(self, cmd: StartApplication) -> bool:
        """예약 시각까지 durable 하게 대기. 취소되면 False."""
        await self._persist(cmd, ApplicationState.SCHEDULED, scheduled_at=self._scheduled_at)
        while True:
            if self._cancelled:
                return False
            assert self._scheduled_at is not None
            remaining = self._scheduled_at - workflow.now()
            if remaining <= timedelta(0):
                return True
            target = self._scheduled_at
            try:
                # reschedule/cancel signal 이 오면 timeout 전에 깨어난다
                await workflow.wait_condition(
                    partial(self._schedule_changed, target), timeout=remaining
                )
            except TimeoutError:
                return True
            if not self._cancelled:
                await self._persist(
                    cmd, ApplicationState.SCHEDULED, scheduled_at=self._scheduled_at
                )

    def _schedule_changed(self, target: datetime) -> bool:
        return self._cancelled or self._scheduled_at != target

    async def _execute(
        self, cmd: StartApplication, job: JobRef, resume_pdf_key: str
    ) -> ApplicationResult:
        self._attempts += 1
        await self._persist(cmd, ApplicationState.EXECUTING)
        recipe = await workflow.execute_activity(
            load_active_recipe,
            job.platform,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=_QUICK,
        )
        # executing~verifying 구간(§2.2)은 _execution.py 로 분리했다 — attempt 감사 로그(§4, §5)
        # 까지 포함하면 이 파일 하나로는 200줄 기준을 크게 넘는다.
        outcome = await _execution.run_execution(
            cmd,
            job,
            recipe,
            self._attempts,
            resume_pdf_key,
            persist=lambda state: self._persist(cmd, state),
        )
        if outcome.state is ApplicationState.NEEDS_HUMAN:
            await self._notify_needs_human(cmd, outcome.reason)
        return await self._finish(
            cmd, outcome.state, outcome.reason, submitted_at=outcome.submitted_at
        )

    async def _notify_needs_human(self, cmd: StartApplication, reason: str) -> None:
        await workflow.execute_activity(
            notify,
            NotifyEvent(kind="NEEDS_HUMAN", application_id=cmd.application_id, message=reason),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK,
        )

    async def _persist(
        self,
        cmd: StartApplication,
        state: ApplicationState,
        *,
        reason: str = "",
        scheduled_at: datetime | None = None,
        submitted_at: datetime | None = None,
    ) -> None:
        self._state = state
        await workflow.execute_activity(
            persist_state,
            PersistState(
                application_id=cmd.application_id,
                workflow_run_id=workflow.info().run_id,
                state=state,
                reason=reason,
                scheduled_at=scheduled_at,
                submitted_at=submitted_at,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_PERSIST,
        )

    async def _finish(
        self,
        cmd: StartApplication,
        state: ApplicationState,
        reason: str,
        submitted_at: datetime | None = None,
    ) -> ApplicationResult:
        await self._persist(cmd, state, reason=reason, submitted_at=submitted_at)
        return ApplicationResult(state=state, reason=reason, submitted_at=submitted_at)

    # ─────────────────────────── signals ───────────────────────────
    def _nonce_ok(self, nonce: str) -> bool:
        """nonce 를 안 보내는 발신자(CLI 등 신뢰된 직접 signal)는 항상 통과시킨다.

        nonce 를 보냈는데 지금 발급된 것과 다르면 오래된 메시지/재전달로 보고 무시한다 (§6).
        """
        return not nonce or nonce == self._decision_nonce

    @workflow.signal
    def approve(self, sig: ApproveSignal) -> None:
        if self._decision is None and self._nonce_ok(sig.nonce):  # 중복 승인 무시 = 멱등
            self._decision = Decision(kind=DecisionKind.APPROVE, scheduled_at=sig.scheduled_at)

    @workflow.signal
    def reject(self, sig: RejectSignal) -> None:
        if self._decision is None and self._nonce_ok(sig.nonce):
            self._decision = Decision(kind=DecisionKind.REJECT, reason=sig.reason)

    @workflow.signal
    def reschedule(self, sig: RescheduleSignal) -> None:
        self._scheduled_at = sig.scheduled_at

    @workflow.signal
    def cancel(self) -> None:
        self._cancelled = True

    # ─────────────────────────── query ───────────────────────────
    @workflow.query
    def state(self) -> StateView:
        """/status 가 DB 대신 이걸 읽는다 (§4.1)."""
        return StateView(
            state=self._state, scheduled_at=self._scheduled_at, attempts=self._attempts
        )
