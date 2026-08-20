"""Recipe 수선 워크플로우 (ARCHITECTURE.md §2.4).

`ApplicationWorkflow`가 RecipeExecutionError 를 만나면 child workflow 로 이 워크플로우를 부른다
(id=`repair-{platform}-{form_hash}`, 동시에 같은 폼이 실패한 다른 지원과 dedupe 된다 —
workflows/_repair.py 참고). 이 파일의 규칙은 `_execution.py`/`_revision.py`와 같다 —
contracts/domain 만 import, workflow.execute_activity 를 직접 쓴다.

정책 검증(§2.4 node D)은 `domain/recipe_policy.check_recipe_policy`가 순수 함수라 activity 없이
여기서 직접 부른다 — `propose_recipe_diff`가 `previous`를 같이 돌려주는 이유가 그거다.
샌드박스 dry-run(node E)은 새 실행 모드가 필요 없다 — 기존 `ExecutionMode.DRY_RUN`(submit
직전까지만)을 그대로 쓴다.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from auto_apply.contracts.activity_defs import (
    execute_application,
    notify,
    promote_recipe,
    propose_recipe_diff,
    request_approval,
    save_recipe_candidate,
)
from auto_apply.contracts.dto import (
    ApproveSignal,
    DecisionRequest,
    ExecuteInput,
    NotifyEvent,
    PromoteRecipeInput,
    RejectSignal,
    RepairInput,
    RepairResult,
)
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.enums import ExecutionMode
from auto_apply.domain.errors import NON_RETRYABLE
from auto_apply.domain.recipe_policy import check_recipe_policy
from auto_apply.workflows._errors import activity_failure

QUEUE_DEFAULT = "default"
QUEUE_AI = "ai"
QUEUE_BROWSER = "browser"

# 다이어그램의 "재시도 < 2?" — LLM diff 가 샌드박스를 통과 못 하면 새 스냅샷으로 딱 한 번만
# 다시 시도한다. 그래도 안 되면 사람에게 넘긴다(무한 루프 방지, MAX_REVIEW_ROUNDS 와 같은 이유).
MAX_SANDBOX_ATTEMPTS = 2
_PROMOTION_TIMEOUT_HOURS = 72
_AI_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    non_retryable_error_types=NON_RETRYABLE,
)
_QUICK = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1))


@workflow.defn
class AutomationRepairWorkflow:
    def __init__(self) -> None:
        self._decision: bool | None = None
        self._nonce: str | None = None

    @workflow.run
    async def run(self, req: RepairInput) -> RepairResult:
        snapshot_key = req.snapshot_key
        candidate: AutomationRecipe | None = None

        for attempt in range(1, MAX_SANDBOX_ATTEMPTS + 1):
            try:
                diff = await workflow.execute_activity(
                    propose_recipe_diff,
                    RepairInput(
                        platform=req.platform,
                        form_hash=req.form_hash,
                        snapshot_key=snapshot_key,
                        failed_version=req.failed_version,
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
            return RepairResult(
                promoted=False, new_version=saved.version, reason="사람이 승격을 승인하지 않았다"
            )

        promoted = await workflow.execute_activity(
            promote_recipe,
            PromoteRecipeInput(platform=req.platform, version=saved.version),
            task_queue=QUEUE_AI,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_QUICK,
        )
        return RepairResult(promoted=True, new_version=promoted.version)

    async def _await_promotion(self, req: RepairInput, candidate: AutomationRecipe) -> bool:
        ticket = await workflow.execute_activity(
            request_approval,
            DecisionRequest(
                # DecisionRequest.application_id 문서 참고 — "{platform}-{form_hash}" 를 담아야
                # telegram/bridge.py 가 wf_id 를 복원할 수 있다.
                application_id=f"{req.platform}-{req.form_hash}",
                workflow_id=workflow.info().workflow_id,
                title=f"{req.platform} recipe v{candidate.version} 승격 승인",
                summary=(
                    f"actions {len(candidate.actions)}개, "
                    f"success_signals={candidate.success_signals}"
                ),
                repair_promotion=True,
            ),
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

    async def _give_up(self, req: RepairInput, reason: str) -> RepairResult:
        await workflow.execute_activity(
            notify,
            NotifyEvent(
                kind="RECIPE_REPAIR_FAILED",
                message=f"{req.platform}({req.form_hash}) recipe 수선 실패: {reason}",
            ),
            task_queue=QUEUE_DEFAULT,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_QUICK,
        )
        return RepairResult(promoted=False, reason=reason)

    def _nonce_ok(self, nonce: str) -> bool:
        return not nonce or nonce == self._nonce

    @workflow.signal
    def approve(self, sig: ApproveSignal) -> None:
        if self._decision is None and self._nonce_ok(sig.nonce):
            self._decision = True

    @workflow.signal
    def reject(self, sig: RejectSignal) -> None:
        if self._decision is None and self._nonce_ok(sig.nonce):
            self._decision = False
