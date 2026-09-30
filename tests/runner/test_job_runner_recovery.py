"""크래시 복구·정지 (§A3, §A9). 행을 RUNNING 으로 심고 기동한다 — 앞선 프로세스가 죽은 상황."""

import asyncio

from auto_apply.contracts.jobs import JobKind, JobRecord, JobStatus, RunRecord
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.enums import RunKind, RunStatus
from auto_apply.domain.failure import RetryPolicy
from auto_apply.runner.recovery import can_rerun
from tests.runner.kit import T0, Rig, wait_until


async def _plant_running(rig: Rig, job_id: str, kind: JobKind, app: str, attempt: int = 1) -> None:
    """죽은 프로세스가 남긴 것: RUNNING job 과 RUNNING run."""
    async with rig.uow() as u:
        await u.jobs.enqueue(
            JobRecord(
                job_id=job_id,
                kind=kind,
                application_id=app,
                attempt=attempt,
                created_at=T0,
                run_after=T0,
            )
        )
        claimed = await u.jobs.claim_next(frozenset({kind}), now=T0)
        assert claimed is not None and claimed.job_id == job_id
        run_kind = RunKind(kind) if kind in {JobKind.FILL, JobKind.REVISE, JobKind.SUBMIT} else None
        if run_kind is not None:
            await u.runs.start(
                RunRecord(run_id=f"run_{job_id}", application_id=app, kind=run_kind, started_at=T0)
            )
        await u.commit()


async def test_crash_recovery_on_start(uow_factory):
    rig = Rig(uow_factory)
    filling = await rig.app_in(S.QUEUED, S.FILLING)
    revising = await rig.app_in(S.QUEUED, S.FILLING, S.AWAITING_APPROVAL, S.REVISING)
    submitting = await rig.app_in(S.QUEUED, S.FILLING, S.AWAITING_APPROVAL, S.SUBMITTING)
    waiting = await rig.app_in(S.QUEUED)
    await _plant_running(rig, "old_fill", JobKind.FILL, filling)
    await _plant_running(rig, "old_revise", JobKind.REVISE, revising)
    await _plant_running(rig, "old_submit", JobKind.SUBMIT, submitting)
    queued = await rig.runner({}).enqueue(JobKind.FILL, application_id=waiting)

    seen: list[tuple[str | None, int]] = []

    async def fill(job: JobRecord) -> None:
        seen.append((job.application_id, job.attempt))

    runner = rig.runner({JobKind.FILL: fill, JobKind.REVISE: fill, JobKind.SUBMIT: fill})
    await runner.start()
    try:

        async def both_ran() -> bool:
            return len(seen) == 2

        await wait_until(both_ran)
        await asyncio.sleep(0.05)
    finally:
        await runner.stop()

    # 대기 중이던 job 은 그대로, 끊긴 fill 은 새 job 으로(크래시는 시도 1회로 센다).
    assert sorted(seen, key=str) == sorted([(filling, 2), (waiting, 1)], key=str)
    assert (await rig.job(queued.job_id)).status is JobStatus.DONE
    for old in ("old_fill", "old_revise", "old_submit"):
        assert (await rig.job(old)).status is JobStatus.INTERRUPTED
    async with rig.uow() as u:
        for old in ("old_fill", "old_revise", "old_submit"):
            run = await u.runs.get(f"run_{old}")
            assert run is not None and run.status is RunStatus.INTERRUPTED
    assert await rig.state(revising) is S.AWAITING_APPROVAL  # 사람이 다시 revise 를 청한다
    assert await rig.state(submitting) is S.INCIDENT  # 제출은 되풀이하지 않는다


async def test_crash_recovery_counts_attempts_and_gives_up(uow_factory):
    """같은 작업이 매번 프로세스를 죽여도 무한히 되살리지 않는다."""
    rig = Rig(uow_factory, retry=RetryPolicy(max_attempts=2, base_delay_s=0))
    app = await rig.app_in(S.QUEUED, S.FILLING)
    await _plant_running(rig, "old", JobKind.FILL, app, attempt=2)
    runner = rig.runner({})
    await runner.start()
    await runner.stop()
    assert await rig.state(app) is S.FAILED
    assert await rig.jobs(JobStatus.QUEUED) == []


async def test_stop_interrupts_running_and_keeps_queued_for_next_start(uow_factory):
    rig = Rig(uow_factory)
    first = await rig.app_in(S.QUEUED)
    second = await rig.app_in(S.QUEUED)
    started = asyncio.Event()

    async def hang(job: JobRecord) -> None:
        started.set()
        await asyncio.Event().wait()  # 정지까지 끝나지 않는 fill run

    runner = rig.runner({JobKind.FILL: rig.to_filling(hang)})
    await runner.start()
    running = await runner.enqueue(JobKind.FILL, application_id=first)
    waiting = await runner.enqueue(JobKind.FILL, application_id=second)
    await asyncio.wait_for(started.wait(), timeout=5)
    await runner.stop()

    assert (await rig.job(running.job_id)).status is JobStatus.INTERRUPTED
    assert (await rig.job(waiting.job_id)).status is JobStatus.QUEUED
    assert await rig.state(first) is S.QUEUED  # FILLING → QUEUED
    queued_now = {j.job_id: j for j in await rig.jobs(JobStatus.QUEUED)}
    assert waiting.job_id in queued_now and len(queued_now) == 2
    [requeued] = [j for jid, j in queued_now.items() if jid != waiting.job_id]
    assert (requeued.application_id, requeued.attempt) == (first, 1)  # 정지는 시도로 세지 않는다

    runner = rig.runner({JobKind.FILL: rig.to_filling()})
    await runner.start()
    try:
        await wait_until(_all_done(rig, waiting.job_id, requeued.job_id))
    finally:
        await runner.stop()
    assert await rig.state(first) is S.AWAITING_APPROVAL
    assert await rig.state(second) is S.AWAITING_APPROVAL


def _all_done(rig: Rig, *job_ids: str):
    async def check() -> bool:
        return [(await rig.job(j)).status for j in job_ids] == [JobStatus.DONE] * len(job_ids)

    return check


async def test_submit_job_is_never_rerun_even_without_application(uow_factory):
    """이중 제출 방지는 지원 건 상태와 별개로 kind 에서도 막는다(겹겹이)."""
    rig = Rig(uow_factory)
    app = await rig.app_in(S.QUEUED)
    for application_id in (None, app):
        job = JobRecord(
            job_id="j",
            kind=JobKind.SUBMIT,
            application_id=application_id,
            created_at=T0,
            run_after=T0,
        )
        assert await can_rerun(rig.apps, job) is False
    fill = JobRecord(job_id="f", kind=JobKind.FILL, application_id=app, created_at=T0, run_after=T0)
    assert await can_rerun(rig.apps, fill) is True
