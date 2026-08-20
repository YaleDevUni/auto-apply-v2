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
from temporalio.exceptions import ActivityError

from auto_apply.contracts.activity_defs import (
    collect_job,
    evaluate_eligibility,
    load_active_recipe,
    notify,
    persist_state,
    request_approval,
)
from auto_apply.contracts.dto import (
    ApplicationResult,
    ApproveSignal,
    Decision,
    DecisionRequest,
    GuidePatchDecisionSignal,
    GuidePatchProposal,
    GuidePatchReviseSignal,
    JobRef,
    NotifyEvent,
    PersistState,
    RejectSignal,
    RescheduleSignal,
    ReviseSignal,
    StartApplication,
    StateView,
)
from auto_apply.domain.enums import ApplicationState, DecisionKind, RevisionScope
from auto_apply.domain.errors import NON_RETRYABLE
from auto_apply.workflows import _execution, _revision
from auto_apply.workflows._errors import activity_failure

# collect_job/evaluate_eligibility 등이 PolicyViolation 같은 non-retryable 도메인 예외를
# 던지면 여기서 바로 멈춰야 한다 — 안 넘기면 같은 실패를 maximum_attempts 만큼 반복하고서야
# 끝난다(실측: 등록 안 된 플랫폼 URL로 5회 재시도 후 실패).
_QUICK = RetryPolicy(
    maximum_attempts=5,
    initial_interval=timedelta(seconds=1),
    non_retryable_error_types=NON_RETRYABLE,
)
_PERSIST = RetryPolicy(maximum_attempts=10, initial_interval=timedelta(seconds=1))


def _guide_patch_summary(proposal: GuidePatchProposal) -> str:
    """한 REVISE(general) 피드백에 여러 지시가 섞여 있으면 patch 도 여러 개다 — 번호를 매겨

    diff 여러 개로 보여준다(메모리 resume-revise-feedback-design).
    """
    parts = []
    for i, p in enumerate(proposal.patches, start=1):
        before = p.old or "(새 규칙 추가)"
        parts.append(f"[{i}] - 기존: {before}\n+ 변경: {p.new}\n사유: {p.rationale or '(없음)'}")
    return "\n\n".join(parts)


@workflow.defn
class ApplicationWorkflow:
    def __init__(self) -> None:
        self._state: ApplicationState = ApplicationState.COLLECTING
        self._decision: Decision | None = None
        self._decision_nonce: str | None = None
        # 가이드 patch 2차 승인용 슬롯 — 본 승인/거절과 nonce/decision 을 분리해 둔다
        # (섞으면 "가이드 반영 승인" 클릭이 "지원 승인"으로 잘못 해석될 수 있다).
        self._guide_decision: _revision.GuidePatchDecision | None = None
        self._guide_nonce: str | None = None
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
        generated = await self._generate_resume(cmd, job, feedback="", round_no=1)
        if generated is None:
            return await self._finish(cmd, ApplicationState.NEEDS_HUMAN, "이력서 생성 실패")

        # ── Human-in-the-loop: 승인 / 거절 / 수정요청(REVISE) ──
        decision, generated = await self._approval_loop(cmd, job, generated)
        if decision is None:
            return await self._finish(cmd, ApplicationState.EXPIRED, "승인 대기 시간 초과")
        if decision.kind is DecisionKind.REJECT:
            return await self._finish(cmd, ApplicationState.REJECTED, decision.reason)
        if decision.kind is DecisionKind.REVISE:
            # _approval_loop 가 MAX_REVISIONS 초과/재생성 실패로 포기하고 반환한 경우다.
            return await self._finish(cmd, ApplicationState.NEEDS_HUMAN, decision.reason)

        # ── Durable timer ──
        self._scheduled_at = decision.scheduled_at or workflow.now()
        if not await self._wait_until_scheduled(cmd):
            return await self._finish(cmd, ApplicationState.CANCELLED, "사용자 취소")

        # ── 실행 ──
        return await self._execute(cmd, job, generated)

    # ─────────────────────── 단계별 헬퍼 ───────────────────────
    async def _generate_resume(
        self, cmd: StartApplication, job: JobRef, *, feedback: str, round_no: int
    ) -> _revision.GeneratedResume | None:
        await self._persist(cmd, ApplicationState.GENERATING_RESUME)
        try:
            return await _revision.generate_and_render(
                cmd,
                job,
                feedback=feedback,
                round_no=round_no,
                persist=lambda state: self._persist(cmd, state),
            )
        except _revision.ResumeGenerationFailed:
            return None

    async def _approval_loop(
        self, cmd: StartApplication, job: JobRef, generated: _revision.GeneratedResume
    ) -> tuple[Decision | None, _revision.GeneratedResume]:
        """승인/거절이 나올 때까지 REVISE 를 반복한다. `cmd.max_revisions`를 넘으면 포기하고

        REVISE 인 채로 반환한다 — 호출자가 그 경우를 NEEDS_HUMAN 으로 마무리한다.
        """
        revisions = 0
        while True:
            decision = await self._await_decision(cmd, job, generated.pdf.blob_key)
            if decision is None or decision.kind is not DecisionKind.REVISE:
                return decision, generated

            self._decision = None  # 다음 라운드를 위해 idempotency 슬롯을 다시 비운다
            revisions += 1
            if revisions > cmd.max_revisions:
                reason = f"수정요청이 {cmd.max_revisions}회를 넘었다"
                return Decision(kind=DecisionKind.REVISE, reason=reason), generated

            if decision.scope is RevisionScope.GENERAL:
                await _revision.revise_guide(
                    cmd,
                    job,
                    decision.feedback,
                    await_guide_decision=lambda p: self._await_guide_patch_decision(cmd, job, p),
                    notify=lambda kind, msg: self._notify(cmd, kind, msg),
                )

            next_generated = await self._generate_resume(
                cmd, job, feedback=decision.feedback, round_no=revisions + 1
            )
            if next_generated is None:
                reason = "이력서 재생성 실패"
                return Decision(kind=DecisionKind.REVISE, reason=reason), generated
            generated = next_generated

    async def _await_guide_patch_decision(
        self, cmd: StartApplication, job: JobRef, proposal: GuidePatchProposal
    ) -> _revision.GuidePatchDecision:
        ticket = await workflow.execute_activity(
            request_approval,
            DecisionRequest(
                application_id=cmd.application_id,
                workflow_id=workflow.info().workflow_id,
                title=f"{job.company} / {job.title} — 이력서 가이드 수정 제안",
                summary=_guide_patch_summary(proposal),
                guide_patch=True,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK,
        )
        self._guide_nonce = ticket.nonce
        try:
            await workflow.wait_condition(
                lambda: self._guide_decision is not None,
                timeout=timedelta(hours=cmd.approval_timeout_hours),
            )
        except TimeoutError:
            return _revision.GuidePatchDecision(kind=DecisionKind.REJECT, feedback="시간 초과")
        decision = self._guide_decision
        assert decision is not None
        # 다음 라운드(코멘트 재제안 또는 다음 REVISE(general))를 위해 슬롯을 비운다
        self._guide_decision = None
        return decision

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
        self, cmd: StartApplication, job: JobRef, generated: _revision.GeneratedResume
    ) -> ApplicationResult:
        self._attempts += 1
        await self._persist(cmd, ApplicationState.EXECUTING)
        try:
            recipe = await workflow.execute_activity(
                load_active_recipe,
                job.platform,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=_QUICK,
            )
        except ActivityError as e:
            # 등록된 active recipe 가 없거나 draft/candidate 밖 status(PolicyViolation, §3)일
            # 때 여기서 그대로 흘리면 워크플로우 자체가 조용히 FAILED 로 죽는다 — CLI 의 `status`
            # 는 내부 `_state` query 만 보여줘서 "아직 실행 중"처럼 보인다(라이브 테스트로 실측,
            # 메모리 workflow-failure-visibility-backlog). 다른 실패 지점들처럼 사람이 알아챌 수
            # 있는 종료 상태(NEEDS_HUMAN)로 정상 종료시킨다 — `_finish` 가 텔레그램 알림도 보낸다.
            _, reason = activity_failure(e)
            reason = f"recipe 조회 실패: {reason}"
            return await self._finish(cmd, ApplicationState.NEEDS_HUMAN, reason)
        # executing~verifying 구간(§2.2)은 _execution.py 로 분리했다 — attempt 감사 로그(§4, §5)
        # 까지 포함하면 이 파일 하나로는 200줄 기준을 크게 넘는다.
        outcome = await _execution.run_execution(
            cmd,
            job,
            recipe,
            self._attempts,
            generated.pdf.blob_key,
            generated.draft.content,
            persist=lambda state: self._persist(cmd, state),
        )
        return await self._finish(
            cmd, outcome.state, outcome.reason, submitted_at=outcome.submitted_at
        )

    async def _notify(self, cmd: StartApplication, kind: str, message: str) -> None:
        await workflow.execute_activity(
            notify,
            NotifyEvent(kind=kind, application_id=cmd.application_id, message=message),
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
        # NEEDS_HUMAN/EXPIRED 는 사람이 자기가 안 시킨 시점에 갑자기 멈춘 것 — 사람이 직접
        # 누른 REJECT/CANCELLED 나 정상 COMPLETED 와 달리 알려주지 않으면 API/로그를 뒤져야만
        # 알 수 있다(실제로 REVISE 최대 횟수 초과로 여기 걸렸는데 텔레그램 알림이 없어서 사용자가
        # 몰랐던 문제, 라이브 세션에서 실측). 여기 한 곳에서 걸어야 새 NEEDS_HUMAN 종료 경로가
        # 늘어나도 알림을 빠뜨리지 않는다.
        if state in (ApplicationState.NEEDS_HUMAN, ApplicationState.EXPIRED):
            await self._notify(cmd, str(state).upper(), reason)
        await self._persist(cmd, state, reason=reason, submitted_at=submitted_at)
        return ApplicationResult(state=state, reason=reason, submitted_at=submitted_at)

    # ─────────────────────────── signals ───────────────────────────
    def _nonce_ok(self, nonce: str) -> bool:
        """nonce 를 안 보내는 발신자(CLI 등 신뢰된 직접 signal)는 항상 통과시킨다.

        nonce 를 보냈는데 지금 발급된 것과 다르면 오래된 메시지/재전달로 보고 무시한다 (§6).
        """
        return not nonce or nonce == self._decision_nonce

    def _guide_nonce_ok(self, nonce: str) -> bool:
        return not nonce or nonce == self._guide_nonce

    @workflow.signal
    def approve(self, sig: ApproveSignal) -> None:
        if self._decision is None and self._nonce_ok(sig.nonce):  # 중복 승인 무시 = 멱등
            self._decision = Decision(kind=DecisionKind.APPROVE, scheduled_at=sig.scheduled_at)

    @workflow.signal
    def reject(self, sig: RejectSignal) -> None:
        if self._decision is None and self._nonce_ok(sig.nonce):
            self._decision = Decision(kind=DecisionKind.REJECT, reason=sig.reason)

    @workflow.signal
    def revise(self, sig: ReviseSignal) -> None:
        if self._decision is None and self._nonce_ok(sig.nonce):
            self._decision = Decision(
                kind=DecisionKind.REVISE, feedback=sig.feedback, scope=sig.scope
            )

    @workflow.signal
    def approve_guide_patch(self, sig: GuidePatchDecisionSignal) -> None:
        if self._guide_decision is None and self._guide_nonce_ok(sig.nonce):
            self._guide_decision = _revision.GuidePatchDecision(kind=DecisionKind.APPROVE)

    @workflow.signal
    def reject_guide_patch(self, sig: GuidePatchDecisionSignal) -> None:
        if self._guide_decision is None and self._guide_nonce_ok(sig.nonce):
            self._guide_decision = _revision.GuidePatchDecision(kind=DecisionKind.REJECT)

    @workflow.signal
    def revise_guide_patch(self, sig: GuidePatchReviseSignal) -> None:
        if self._guide_decision is None and self._guide_nonce_ok(sig.nonce):
            self._guide_decision = _revision.GuidePatchDecision(
                kind=DecisionKind.REVISE, feedback=sig.feedback
            )

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
