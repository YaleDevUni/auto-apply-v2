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
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError

from auto_apply.contracts.activity_defs import (
    collect_job,
    evaluate_eligibility,
    execute_application,
    load_active_recipe,
    notify,
    persist_state,
    render_pdf,
    request_approval,
    verify_submission,
)
from auto_apply.contracts.dto import (
    ApplicationResult,
    ApproveSignal,
    Decision,
    DecisionRequest,
    ExecuteInput,
    ExecutionContext,
    GenerateResumeRequest,
    JobRef,
    NotifyEvent,
    PersistState,
    RejectSignal,
    RescheduleSignal,
    StartApplication,
    StateView,
    VerifyInput,
)
from auto_apply.domain.enums import ApplicationState, DecisionKind, ExecutionMode
from auto_apply.workflows.resume import ResumeWorkflow

QUEUE_AI = "ai"
QUEUE_BROWSER = "browser"

_QUICK = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1))
_PERSIST = RetryPolicy(maximum_attempts=10, initial_interval=timedelta(seconds=1))


@workflow.defn
class ApplicationWorkflow:
    def __init__(self) -> None:
        self._state: ApplicationState = ApplicationState.COLLECTING
        self._decision: Decision | None = None
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
        return await self._execute(cmd, job)

    # ─────────────────────── 단계별 헬퍼 ───────────────────────
    async def _await_decision(
        self, cmd: StartApplication, job: JobRef, pdf_key: str
    ) -> Decision | None:
        await self._persist(cmd, ApplicationState.AWAITING_APPROVAL)
        await workflow.execute_activity(
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

    async def _execute(self, cmd: StartApplication, job: JobRef) -> ApplicationResult:
        self._attempts += 1
        await self._persist(cmd, ApplicationState.EXECUTING)
        recipe = await workflow.execute_activity(
            load_active_recipe,
            job.platform,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=_QUICK,
        )
        mode = self._resolve_mode(cmd, recipe.status)

        try:
            result = await workflow.execute_activity(
                execute_application,
                ExecuteInput(
                    recipe=recipe,
                    ctx=ExecutionContext(
                        application_id=cmd.application_id,
                        attempt=self._attempts,
                        profile={"email": "user@example.com", "name": "지원자"},
                    ),
                    mode=mode,
                ),
                task_queue=QUEUE_BROWSER,
                start_to_close_timeout=timedelta(minutes=15),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
        except ActivityError as e:
            # Temporal 은 예외를 ApplicationError 로 감싸며 원래 클래스는 .type 문자열로 남는다.
            # 그래서 isinstance 가 아니라 type 비교를 해야 한다.
            failure_type = e.cause.type if isinstance(e.cause, ApplicationError) else None
            reason = f"{failure_type or 'unknown'}: {e.cause}"
            if failure_type == "RecipeExecutionError":
                # M4: 여기서 AutomationRepairWorkflow 로 분기한다 (§2.4).
                await self._notify_needs_human(cmd, reason)
                return await self._finish(cmd, ApplicationState.NEEDS_HUMAN, reason)
            await self._notify_needs_human(cmd, reason)
            return await self._finish(cmd, ApplicationState.NEEDS_HUMAN, reason)

        if mode is ExecutionMode.DRY_RUN:
            return await self._finish(cmd, ApplicationState.COMPLETED, f"dry_run: {result.detail}")

        await self._persist(cmd, ApplicationState.VERIFYING)
        verified = await workflow.execute_activity(
            verify_submission,
            VerifyInput(application_id=cmd.application_id, platform=job.platform),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_QUICK,
        )
        if not verified.verified:
            reason = f"제출 확인 실패: {verified.detail}"
            await self._notify_needs_human(cmd, reason)
            return await self._finish(cmd, ApplicationState.NEEDS_HUMAN, reason)

        return await self._finish(
            cmd, ApplicationState.COMPLETED, result.detail, submitted_at=result.submitted_at
        )

    @staticmethod
    def _resolve_mode(cmd: StartApplication, recipe_status: str) -> ExecutionMode:
        """제출 여부 결정. 안전한 쪽이 기본값이다 (§9.5)."""
        if cmd.dry_run_only:
            return ExecutionMode.DRY_RUN
        if recipe_status == "candidate":
            return ExecutionMode.SUPERVISED
        return ExecutionMode.LIVE

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
    @workflow.signal
    def approve(self, sig: ApproveSignal) -> None:
        if self._decision is None:  # 중복 승인 무시 = 멱등 (버튼은 두 번 눌린다)
            self._decision = Decision(kind=DecisionKind.APPROVE, scheduled_at=sig.scheduled_at)

    @workflow.signal
    def reject(self, sig: RejectSignal) -> None:
        if self._decision is None:
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
