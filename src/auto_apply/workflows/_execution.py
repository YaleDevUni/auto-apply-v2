"""ApplicationWorkflow 의 executing~verifying 구간 헬퍼 (ARCHITECTURE.md §2.2, §5).

`application.py`가 200줄을 넘기지 않도록 이 구간만 분리했다 — 책임은 여전히
`ApplicationWorkflow.run` 하나이고, 이 모듈은 그 안에서만 호출되는 순수 워크플로우 코드다
(즉 contracts/domain 만 import, workflow.execute_activity 를 직접 쓴다).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from auto_apply.contracts.activity_defs import (
    execute_application,
    record_attempt,
    verify_submission,
)
from auto_apply.contracts.dto import (
    ApplicationAttempt,
    ExecuteInput,
    ExecutionContext,
    JobRef,
    StartApplication,
    VerifyInput,
    VerifyResult,
)
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.enums import ApplicationState, AttemptOutcome, ExecutionMode
from auto_apply.domain.errors import NON_RETRYABLE
from auto_apply.workflows._errors import activity_failure

QUEUE_BROWSER = "browser"
# AuthRequired(예: wanted storage_state 만료) 등 재시도로 안 풀리는 실패를 5번 반복하지
# 않게 non_retryable_error_types 를 건다 — application.py 의 같은 이름 _QUICK 과 같은 이유.
_QUICK = RetryPolicy(
    maximum_attempts=5,
    initial_interval=timedelta(seconds=1),
    non_retryable_error_types=NON_RETRYABLE,
)
_PERSIST = RetryPolicy(maximum_attempts=10, initial_interval=timedelta(seconds=1))


@dataclass(frozen=True)
class RepairTrigger:
    """`_handle_execution_failure` 가 RecipeExecutionError 를 만났을 때만 채운다 — 호출자

    (`application.py`)가 이 필드로 "repair 를 시도할 가치가 있는 실패였는가"를 판단한다.
    verify_submission 이 실제로는 제출됐다고 확인해준 복구 경로에서는 안 채워진다(§5, 이미
    성공했는데 repair 를 돌릴 이유가 없다).
    """

    snapshot_key: str
    form_hash: str
    failed_version: int
    failure_reason: str
    # BrowserActivities 가 details=[snapshot_key, form_hash, failed_action_index] 로 실어
    # 보낸다 — 옛 details 모양(2개)만 있는 경우를 대비해 None 허용(domain/recipe_repair.py
    # bump_goto_timeout 이 없으면 결정론적 경로를 건너뛰고 LLM 경로로 떨어진다).
    failed_action_index: int | None


@dataclass(frozen=True)
class ExecutionOutcome:
    state: ApplicationState  # COMPLETED | NEEDS_HUMAN
    reason: str
    submitted_at: datetime | None = None
    repair: RepairTrigger | None = None


def resolve_mode(cmd: StartApplication, recipe_status: str) -> ExecutionMode:
    """제출 여부 결정. 안전한 쪽이 기본값이다 (§9.5)."""
    if cmd.dry_run_only:
        return ExecutionMode.DRY_RUN
    if recipe_status == "candidate":
        return ExecutionMode.SUPERVISED
    return ExecutionMode.LIVE


def build_context(
    cmd: StartApplication,
    attempt_no: int,
    resume_pdf_key: str,
    resume_content: dict[str, object],
) -> ExecutionContext:
    """`run_execution`과 repair 의 샌드박스 dry-run(workflows/_repair.py)이 같은 모양의

    ExecutionContext 를 만들도록 한곳으로 뺐다 — repair 가 검증해야 하는 건 정확히 이
    실행이 실패했을 때 쓰려던 값이다.
    """
    return ExecutionContext(
        application_id=cmd.application_id,
        attempt=attempt_no,
        profile={
            "job_url": cmd.job_url,
            "name": str(resume_content.get("name", "")),
            "email": str(resume_content.get("email", "")),
            "phone": str(resume_content.get("phone", "")),
            # 플랫폼 화면에 그대로 노출되는 이름이라 selector 매칭 기준이 된다
            # (실행기가 blob key 의 마지막 경로 요소를 업로드 파일명으로 쓴다).
            "resume_filename": Path(resume_pdf_key).name,
            # 비어 있을 수 있다(카테고리 판정이 안 됐거나 매칭되는 파일이 없음) —
            # 이 값을 쓰는 Recipe 액션은 optional=True 로 짜서 없으면 건너뛴다.
            "portfolio_filename": str(resume_content.get("portfolio_filename", "")),
        },
        upload_keys={"resume": resume_pdf_key},
    )


async def run_execution(
    cmd: StartApplication,
    job: JobRef,
    recipe: AutomationRecipe,
    attempt_no: int,
    resume_pdf_key: str,
    resume_content: dict[str, object],
    persist: Callable[[ApplicationState], Awaitable[None]],
) -> ExecutionOutcome:
    """load_active_recipe 이후 ~ 최종 상태 결정까지. 매 시도를 `application_attempts`에

    감사 로그로 남긴다 (§4) — submit 직전 UNKNOWN("submitting") 선기록 → 결과로 덮어쓰기 (§5).
    """
    mode = resolve_mode(cmd, recipe.status)
    started_at = workflow.now()

    # submit 직전에 UNKNOWN 으로 먼저 기록한다. 이 activity 이후 크래시가 나도 "결론이
    # 안 난 시도"가 감사 로그에 남는다 — 재개 시 verify_submission 을 먼저 돌리는 근거.
    await _record_attempt(cmd, recipe, mode, attempt_no, AttemptOutcome.UNKNOWN, started_at)

    ctx = build_context(cmd, attempt_no, resume_pdf_key, resume_content)
    try:
        result = await workflow.execute_activity(
            execute_application,
            ExecuteInput(recipe=recipe, ctx=ctx, mode=mode),
            task_queue=QUEUE_BROWSER,
            # SUPERVISED 는 이 activity 안에서 사람의 체크포인트 승인을 기다린다
            # (기본 checkpoint_timeout_minutes=30). 15분이면 승인 전에 activity 가 먼저
            # StartToClose 로 죽어서 recipe 를 처음부터 다시 실행한다 — 죽은 워커는
            # heartbeat_timeout 30초가 이미 잡아주므로 이 상한은 넉넉해도 안전하다.
            start_to_close_timeout=timedelta(minutes=45),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
    except ActivityError as e:
        return await _handle_execution_failure(cmd, job, recipe, attempt_no, mode, started_at, e)

    if mode is ExecutionMode.DRY_RUN:
        await _record_attempt(
            cmd,
            recipe,
            mode,
            attempt_no,
            result.outcome,
            started_at,
            artifact_keys=result.artifact_keys,
            detail=result.detail,
        )
        return ExecutionOutcome(ApplicationState.COMPLETED, f"dry_run: {result.detail}")

    await persist(ApplicationState.VERIFYING)
    verified = await _verify(cmd, job, started_at)
    if not verified.verified:
        reason = f"제출 확인 실패: {verified.detail}"
        await _record_attempt(
            cmd,
            recipe,
            mode,
            attempt_no,
            AttemptOutcome.UNKNOWN,
            started_at,
            error_code="verify_failed",
            artifact_keys=result.artifact_keys,
            detail=reason,
        )
        return ExecutionOutcome(ApplicationState.NEEDS_HUMAN, reason)

    await _record_attempt(
        cmd,
        recipe,
        mode,
        attempt_no,
        AttemptOutcome.SUCCEEDED,
        started_at,
        submitted_at=result.submitted_at,
        artifact_keys=result.artifact_keys,
        detail=result.detail,
    )
    return ExecutionOutcome(ApplicationState.COMPLETED, result.detail, result.submitted_at)


async def _handle_execution_failure(
    cmd: StartApplication,
    job: JobRef,
    recipe: AutomationRecipe,
    attempt_no: int,
    mode: ExecutionMode,
    started_at: datetime,
    e: ActivityError,
) -> ExecutionOutcome:
    failure_type, reason = activity_failure(e)
    snapshot_key = ""
    form_hash = ""
    failed_action_index: int | None = None
    cause = e.cause
    if failure_type == "RecipeExecutionError" and isinstance(cause, ApplicationError):
        # BrowserActivities 가 details=[snapshot_key, form_hash, failed_action_index] 로 실어
        # 보낸다 (§2.4 repair 용).
        snapshot_key = str(cause.details[0]) if cause.details else ""
        form_hash = str(cause.details[1]) if len(cause.details) > 1 else recipe.form_hash
        if len(cause.details) > 2 and cause.details[2] is not None:
            failed_action_index = int(cause.details[2])

    # 부분 제출 위험 방어 (§5): 실행 activity 가 실패해도 실제로는 submit 이 됐을 수 있다.
    # dry_run 은 애초에 submit 을 안 하니 확인할 게 없다.
    if mode is not ExecutionMode.DRY_RUN:
        verified = await _verify(cmd, job, started_at)
        if verified.verified:
            await _record_attempt(
                cmd,
                recipe,
                mode,
                attempt_no,
                AttemptOutcome.SUCCEEDED,
                started_at,
                detail=f"복구됨({reason}) → verify 로 제출 확인: {verified.detail}",
            )
            recovered = f"실행 오류 후 제출 확인됨: {reason}"
            return ExecutionOutcome(ApplicationState.COMPLETED, recovered)

    await _record_attempt(
        cmd,
        recipe,
        mode,
        attempt_no,
        AttemptOutcome.FAILED,
        started_at,
        error_code=failure_type or "unknown",
        snapshot_key=snapshot_key,
        detail=reason,
    )
    repair = (
        RepairTrigger(
            snapshot_key=snapshot_key,
            form_hash=form_hash,
            failed_version=recipe.version,
            failure_reason=reason,
            failed_action_index=failed_action_index,
        )
        if failure_type == "RecipeExecutionError" and snapshot_key
        else None
    )
    return ExecutionOutcome(ApplicationState.NEEDS_HUMAN, reason, repair=repair)


async def _verify(cmd: StartApplication, job: JobRef, started_at: datetime) -> VerifyResult:
    """verify_submission activity 호출을 감싼다.

    activity 실행 자체가 실패해도(예: `AuthRequired` — wanted storage_state 만료) 워크플로우를
    죽이지 않고 "확인 안 됨"으로 안전하게 떨어뜨린다 — 부분 제출 위험 방어(§5)와 같은 원칙,
    거짓 확인보다 미확인이 낫다. `_finish`가 NEEDS_HUMAN 사유로 이 detail 을 그대로 텔레그램에
    실어 보내서 "왜 확인이 안 됐는지"가 사람에게 그대로 드러난다.
    """
    try:
        return await workflow.execute_activity(
            verify_submission,
            VerifyInput(
                application_id=cmd.application_id,
                platform=job.platform,
                job_id=job.job_id,
                since=started_at,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_QUICK,
        )
    except ActivityError as e:
        _, reason = activity_failure(e)
        return VerifyResult(verified=False, detail=f"제출 확인 activity 실패: {reason}")


async def _record_attempt(
    cmd: StartApplication,
    recipe: AutomationRecipe,
    mode: ExecutionMode,
    attempt_no: int,
    outcome: AttemptOutcome,
    started_at: datetime,
    *,
    submitted_at: datetime | None = None,
    error_code: str = "",
    snapshot_key: str = "",
    artifact_keys: list[str] | None = None,
    detail: str = "",
) -> None:
    await workflow.execute_activity(
        record_attempt,
        ApplicationAttempt(
            application_id=cmd.application_id,
            attempt=attempt_no,
            recipe_platform=recipe.platform,
            recipe_version=recipe.version,
            mode=mode,
            outcome=outcome,
            started_at=started_at,
            ended_at=None if outcome is AttemptOutcome.UNKNOWN else workflow.now(),
            submitted_at=submitted_at,
            error_code=error_code,
            snapshot_key=snapshot_key,
            artifact_keys=artifact_keys or [],
            detail=detail,
        ),
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=_PERSIST,
    )
