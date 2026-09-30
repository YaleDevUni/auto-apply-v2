"""크래시·정지 복구 (§A3, §A9). 러너가 아무것도 돌리지 않을 때(기동 직후·정지 뒤)만 부른다.

RUNNING 인 run·job 을 INTERRUPTED 로 닫고, 지원 건은 직전 재개 가능 상태로 되돌린다
(FILLING→QUEUED · REVISING→AWAITING_APPROVAL · SUBMITTING→INCIDENT). 되돌린 결과가 다시 돌릴
일이면(지원 건이 QUEUED, 또는 지원 건 없는 작업) 같은 작업을 새 job 행으로 줄 세운다.
"""

from collections.abc import Callable

import structlog

from auto_apply.contracts.jobs import NEVER_RETRY_KINDS, JobRecord
from auto_apply.domain.enums import ApplicationState
from auto_apply.domain.errors import NotFound
from auto_apply.domain.failure import FailureKind, RetryPolicy
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.repository import UnitOfWork
from auto_apply.services.application import ApplicationService

log = structlog.get_logger(__name__)


async def can_rerun(apps: ApplicationService, job: JobRecord) -> bool:
    """같은 작업을 다시 돌려도 되는가. 제출은 절대 되풀이하지 않는다(이중 제출)."""
    if job.kind in NEVER_RETRY_KINDS:
        return False
    if job.application_id is None:
        return True
    try:
        return await apps.current_state(job.application_id) is ApplicationState.QUEUED
    except NotFound:
        return False


async def recover_interrupted(
    uow: Callable[[], UnitOfWork],
    apps: ApplicationService,
    clock: Clock,
    idgen: IdGen,
    retry: RetryPolicy,
    *,
    count_attempt: bool,
) -> list[JobRecord]:
    """`count_attempt`: 크래시는 시도 1회로 센다(같은 작업이 매번 죽이는 루프를 끊는다),
    사용자가 앱을 끈 정지는 세지 않는다. 새로 줄 세운 job 들을 돌려준다."""
    reason = "crash_recovery" if count_attempt else "shutdown"
    now = clock.now()
    async with uow() as u:
        run_ids = await u.runs.interrupt_running(at=now)
        jobs = await u.jobs.interrupt_running(at=now)
        await u.commit()
    recovered = await apps.recover_interrupted(reason=reason)
    for run_id in run_ids:
        log.warning("run.interrupted", application_id=None, run_id=run_id, reason=reason)
    for application_id, state in recovered.items():
        log.warning(
            "application.recovered",
            application_id=application_id,
            run_id=None,
            to_state=str(state),
            reason=reason,
        )

    requeued: list[JobRecord] = []
    for job in jobs:
        ctx = {"application_id": job.application_id, "run_id": None, "job_id": job.job_id}
        if not await can_rerun(apps, job):
            log.warning("job.interrupted", requeued=False, reason=reason, **ctx)
            continue
        if count_attempt and not retry.should_retry(job.attempt):
            log.error("job.interrupted_exhausted", attempt=job.attempt, **ctx)
            if job.application_id is not None:
                await apps.settle_failure(
                    job.application_id,
                    FailureKind.FATAL,
                    run_id=None,
                    reason=f"{reason}: 재시도 한도({retry.max_attempts}회)",
                )
            continue
        fresh = JobRecord(
            job_id=idgen.new_id("job"),
            kind=job.kind,
            application_id=job.application_id,
            attempt=job.attempt + 1 if count_attempt else job.attempt,
            payload=job.payload,
            created_at=now,
            run_after=now,
        )
        async with uow() as u:
            await u.jobs.enqueue(fresh)
            await u.commit()
        requeued.append(fresh)
        log.warning("job.interrupted", requeued=True, new_job_id=fresh.job_id, reason=reason, **ctx)
    return requeued
