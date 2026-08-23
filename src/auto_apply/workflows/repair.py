"""Recipe 수선 워크플로우 (ARCHITECTURE.md §2.4, §2.4a).

`ApplicationWorkflow`가 RecipeExecutionError 를 만나면 child workflow 로 이 워크플로우를 부른다
(id=`repair-{platform}-{form_hash}`, 동시에 같은 폼이 실패한 다른 지원과 dedupe 된다 —
workflows/_repair.py 참고). 이 파일의 규칙은 `_execution.py`/`_revision.py`와 같다 —
contracts/domain 만 import, workflow.execute_activity 를 직접 쓴다.

**첫 단계는 LLM 이 아니라 사람이다 (§2.4a).** "실행이 실패했다"가 곧 "recipe 가 깨졌다"는
아니다 — 이미 지원한 공고/로그인 만료/마감 공고에서도 똑같은 selector timeout 이 난다. 그래서
스냅샷 판정(`diagnose_recipe_failure`)을 근거로 붙여 사람에게 먼저 묻고, 사람이 확정했을
때만 recipe 를 격리(제출 중단)하고 수선을 시작한다. 확정 전에는 아무것도 안 바꾸므로 다른
지원 건은 계속 제출된다.

정책 검증(§2.4 node D)은 `domain/recipe_policy.check_recipe_policy`가 순수 함수라 activity 없이
여기서 직접 부른다 — `propose_recipe_diff`가 `previous`를 같이 돌려주는 이유가 그거다.
샌드박스 dry-run(node E)은 새 실행 모드가 필요 없다 — 기존 `ExecutionMode.DRY_RUN`(submit
직전까지만)을 그대로 쓴다. 사람에게 보낼 문구는 `_repair_messages.py`(순수 함수)에 있다.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from auto_apply.contracts.activity_defs import (
    diagnose_recipe_failure,
    execute_application,
    notify,
    promote_recipe,
    propose_recipe_diff,
    quarantine_recipe,
    request_approval,
    save_recipe_candidate,
)
from auto_apply.contracts.dto import (
    ApproveSignal,
    ExecuteInput,
    NotifyEvent,
    PromoteRecipeInput,
    QuarantineRecipeInput,
    RejectSignal,
    RepairDiagnosis,
    RepairInput,
    RepairResult,
)
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.enums import ExecutionMode
from auto_apply.domain.errors import NON_RETRYABLE
from auto_apply.domain.recipe_diagnosis import PageVerdict
from auto_apply.domain.recipe_policy import check_recipe_policy
from auto_apply.workflows import _repair_messages as msg
from auto_apply.workflows._errors import activity_failure

QUEUE_DEFAULT = "default"
QUEUE_AI = "ai"
QUEUE_BROWSER = "browser"

# 다이어그램의 "재시도 < 2?" — LLM diff 가 샌드박스를 통과 못 하면 새 스냅샷으로 딱 한 번만
# 다시 시도한다. 그래도 안 되면 사람에게 넘긴다(무한 루프 방지, MAX_REVIEW_ROUNDS 와 같은 이유).
MAX_SANDBOX_ATTEMPTS = 2
_PROMOTION_TIMEOUT_HOURS = 72
# 승격 승인(72h)보다 짧다 — 이 단계는 무응답의 기본값("아무것도 안 한다")이 안전한 쪽이고,
# 대기하는 동안 트리거가 된 지원 건이 REPAIRING 에 묶여 있기 때문이다(§2.4a).
_CONFIRM_TIMEOUT_HOURS = 24
_AI_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    non_retryable_error_types=NON_RETRYABLE,
)
_QUICK = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1))


@workflow.defn
class AutomationRepairWorkflow:
    def __init__(self) -> None:
        # 확인(§2.4a)과 승격(§2.4)은 슬롯/nonce 를 따로 둔다 — 하나로 합치면 "수선해도 된다"
        # 클릭이 "새 recipe 를 active 로 올려도 된다"로 잘못 소비될 수 있다
        # (ApplicationWorkflow 가 본 승인과 가이드 patch 승인을 분리한 것과 같은 이유).
        self._confirmed: bool | None = None
        self._confirm_nonce: str | None = None
        self._decision: bool | None = None
        self._nonce: str | None = None
        self._quarantined = False

    @workflow.run
    async def run(self, req: RepairInput) -> RepairResult:
        snapshot_key = req.snapshot_key
        failure_reason = req.failure_reason
        failed_action_index = req.failed_action_index
        candidate: AutomationRecipe | None = None

        diagnosis = await self._diagnose(req)
        if not await self._await_broken_confirmation(req, diagnosis):
            await self._notify(msg.stand_down_event(req, diagnosis))
            return RepairResult(
                promoted=False,
                reason=f"사람이 recipe 문제가 아니라고 판단했다 ({diagnosis.summary})",
            )
        await self._quarantine(req)

        for attempt in range(1, MAX_SANDBOX_ATTEMPTS + 1):
            try:
                diff = await workflow.execute_activity(
                    propose_recipe_diff,
                    RepairInput(
                        platform=req.platform,
                        form_hash=req.form_hash,
                        snapshot_key=snapshot_key,
                        failed_version=req.failed_version,
                        failure_reason=failure_reason,
                        failed_action_index=failed_action_index,
                        ctx=req.ctx,
                    ),
                    task_queue=QUEUE_AI,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_AI_RETRY,
                )
            except ActivityError as e:
                _, reason = activity_failure(e)
                return await self._give_up(req, f"LLM diff 제안 실패: {reason}")

            verdict = check_recipe_policy(diff.candidate, previous=diff.previous)
            if not verdict.safe:
                codes = ", ".join(b.code for b in verdict.blockers)
                return await self._give_up(req, f"정책 위반: {codes}")
            candidate = diff.candidate

            try:
                await workflow.execute_activity(
                    execute_application,
                    ExecuteInput(recipe=candidate, ctx=req.ctx, mode=ExecutionMode.DRY_RUN),
                    task_queue=QUEUE_BROWSER,
                    start_to_close_timeout=timedelta(minutes=15),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                break  # 샌드박스 통과 — for/else 를 안 타고 아래로 내려간다
            except ActivityError as e:
                failure_type, reason = activity_failure(e)
                if failure_type == "RecipeExecutionError" and attempt < MAX_SANDBOX_ATTEMPTS:
                    cause = e.cause
                    if isinstance(cause, ApplicationError) and cause.details:
                        snapshot_key = str(cause.details[0])
                        if len(cause.details) > 2 and cause.details[2] is not None:
                            failed_action_index = int(cause.details[2])
                        else:
                            failed_action_index = None
                    # 다음 시도의 [실패 사유]를 이번 샌드박스 실패로 갱신한다 — 안 갱신하면
                    # 재프롬프트가 계속 최초 실패 사유(예: 이미 고쳐진 timeout)를 보고 판단해
                    # 이번에 새로 난 실패(예: 다른 selector 문제)를 놓친다.
                    failure_reason = reason
                    candidate = None
                    continue
                return await self._give_up(req, f"샌드박스 dry-run 실패: {reason}")

        # for 루프는 항상 break(샌드박스 통과) 또는 위의 return 중 하나로 끝난다 — attempt 가
        # MAX_SANDBOX_ATTEMPTS 에 도달하면 continue 조건이 거짓이라 반드시 return 한다.
        assert candidate is not None
        saved = await workflow.execute_activity(
            save_recipe_candidate,
            candidate.model_copy(update={"status": "candidate"}),
            task_queue=QUEUE_AI,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_QUICK,
        )

        if not await self._await_promotion(req, saved):
            return await self._give_up(req, "사람이 승격을 승인하지 않았다", version=saved.version)

        promoted = await workflow.execute_activity(
            promote_recipe,
            PromoteRecipeInput(platform=req.platform, version=saved.version),
            task_queue=QUEUE_AI,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_QUICK,
        )
        # promote() 가 옛 active/quarantined 버전을 함께 deprecated 로 내린다 — 승격이 곧
        # 격리 해제다(ports/recipe_source.py).
        return RepairResult(promoted=True, new_version=promoted.version)

    # ────────────────────── §2.4a 사람 확인 · 격리 ──────────────────────
    async def _diagnose(self, req: RepairInput) -> RepairDiagnosis:
        """판정 실패가 확인 절차 자체를 막아선 안 된다 — 스냅샷을 못 읽어도 사람에게는

        물어봐야 한다. 그래서 실패하면 "판정 불가"로 떨어뜨리고 계속 간다.
        """
        try:
            return await workflow.execute_activity(
                diagnose_recipe_failure,
                req,
                task_queue=QUEUE_AI,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_QUICK,
            )
        except ActivityError as e:
            _, reason = activity_failure(e)
            return RepairDiagnosis(
                verdict=PageVerdict.RECIPE_SUSPECTED, hint=f"페이지 판정 실패: {reason}"
            )

    async def _await_broken_confirmation(
        self, req: RepairInput, diagnosis: RepairDiagnosis
    ) -> bool:
        ticket = await workflow.execute_activity(
            request_approval,
            msg.confirm_request(req, workflow.info().workflow_id, diagnosis),
            task_queue=QUEUE_DEFAULT,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK,
        )
        self._confirm_nonce = ticket.nonce
        try:
            await workflow.wait_condition(
                lambda: self._confirmed is not None,
                timeout=timedelta(hours=_CONFIRM_TIMEOUT_HOURS),
            )
        except TimeoutError:
            return False  # 무응답의 기본값은 "아무것도 안 한다" — 제출을 막지 않는다
        assert self._confirmed is not None
        return self._confirmed

    async def _quarantine(self, req: RepairInput) -> None:
        """격리 실패가 수선을 막지는 않는다 — 제출이 안 멈춘 채로 수선만 진행하고, 그 사실을

        사람에게 알린다. 여기서 워크플로우를 죽이면 "깨졌다고 확정했는데 아무 일도 안 일어남"이
        된다.
        """
        held: AutomationRecipe | None = None
        try:
            held = await workflow.execute_activity(
                quarantine_recipe,
                QuarantineRecipeInput(
                    platform=req.platform,
                    reason=f"v{req.failed_version} 실행 실패를 사람이 확정: {req.failure_reason}",
                ),
                task_queue=QUEUE_AI,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_QUICK,
            )
        except ActivityError:
            held = None
        self._quarantined = held is not None
        await self._notify(msg.started_event(req, held))

    # ────────────────────────── §2.4 승격 승인 ──────────────────────────
    async def _await_promotion(self, req: RepairInput, candidate: AutomationRecipe) -> bool:
        ticket = await workflow.execute_activity(
            request_approval,
            msg.promotion_request(req, workflow.info().workflow_id, candidate),
            task_queue=QUEUE_DEFAULT,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK,
        )
        self._nonce = ticket.nonce
        try:
            await workflow.wait_condition(
                lambda: self._decision is not None,
                timeout=timedelta(hours=_PROMOTION_TIMEOUT_HOURS),
            )
        except TimeoutError:
            return False
        assert self._decision is not None
        return self._decision

    async def _give_up(
        self, req: RepairInput, reason: str, *, version: int | None = None
    ) -> RepairResult:
        await self._notify(msg.failed_event(req, reason, quarantined=self._quarantined))
        return RepairResult(promoted=False, new_version=version, reason=reason)

    async def _notify(self, event: NotifyEvent) -> None:
        await workflow.execute_activity(
            notify,
            event,
            task_queue=QUEUE_DEFAULT,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK,
        )

    # ─────────────────────────── signals ───────────────────────────
    @workflow.signal
    def confirm_broken(self, sig: ApproveSignal) -> None:
        """ "진짜 깨졌다" — 격리 + 수선 시작 (§2.4a). 텔레그램 `qa` 콜백."""
        if self._confirmed is None and (not sig.nonce or sig.nonce == self._confirm_nonce):
            self._confirmed = True

    @workflow.signal
    def deny_broken(self, sig: RejectSignal) -> None:
        """ "recipe 문제가 아니다" — 아무것도 안 바꾸고 끝낸다. 텔레그램 `qr` 콜백."""
        if self._confirmed is None and (not sig.nonce or sig.nonce == self._confirm_nonce):
            self._confirmed = False

    @workflow.signal
    def approve(self, sig: ApproveSignal) -> None:
        if self._decision is None and self._nonce_ok(sig.nonce):
            self._decision = True

    @workflow.signal
    def reject(self, sig: RejectSignal) -> None:
        if self._decision is None and self._nonce_ok(sig.nonce):
            self._decision = False

    def _nonce_ok(self, nonce: str) -> bool:
        return not nonce or nonce == self._nonce
