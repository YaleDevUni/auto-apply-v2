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
)
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.enums import ApplicationState, AttemptOutcome, ExecutionMode

QUEUE_BROWSER = "browser"
_QUICK = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1))
_PERSIST = RetryPolicy(maximum_attempts=10, initial_interval=timedelta(seconds=1))


@dataclass(frozen=True)
class ExecutionOutcome:
    state: ApplicationState  # COMPLETED | NEEDS_HUMAN
    reason: str
    submitted_at: datetime | None = None


def resolve_mode(cmd: StartApplication, recipe_status: str) -> ExecutionMode:
    """제출 여부 결정. 안전한 쪽이 기본값이다 (§9.5)."""
    if cmd.dry_run_only:
        return ExecutionMode.DRY_RUN
    if recipe_status == "candidate":
        return ExecutionMode.SUPERVISED
    return ExecutionMode.LIVE


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

    try:
        result = await workflow.execute_activity(
            execute_application,
            ExecuteInput(
                recipe=recipe,
                ctx=ExecutionContext(
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
                ),
                mode=mode,
            ),
            task_queue=QUEUE_BROWSER,
            start_to_close_timeout=timedelta(minutes=15),
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
    verified = await workflow.execute_activity(
        verify_submission,
        VerifyInput(application_id=cmd.application_id, platform=job.platform),
        start_to_close_timeout=timedelta(minutes=5),
        retry_policy=_QUICK,
    )
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
    # Temporal 은 예외를 ApplicationError 로 감싸며 원래 클래스는 .type 문자열로 남는다.
    # 그래서 isinstance 가 아니라 type 비교를 해야 한다.
    cause = e.cause
    failure_type = cause.type if isinstance(cause, ApplicationError) else None
    reason = f"{failure_type or 'unknown'}: {cause}"
    snapshot_key = ""
    if failure_type == "RecipeExecutionError" and isinstance(cause, ApplicationError):
        # BrowserActivities 가 details[0] 에 snapshot_key 를 실어 보낸다 (M4 repair 용).
        snapshot_key = str(cause.details[0]) if cause.details else ""

    # 부분 제출 위험 방어 (§5): 실행 activity 가 실패해도 실제로는 submit 이 됐을 수 있다.
    # dry_run 은 애초에 submit 을 안 하니 확인할 게 없다.
    if mode is not ExecutionMode.DRY_RUN:
        verified = await workflow.execute_activity(
            verify_submission,
            VerifyInput(application_id=cmd.application_id, platform=job.platform),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_QUICK,
        )
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
    return ExecutionOutcome(ApplicationState.NEEDS_HUMAN, reason)


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
