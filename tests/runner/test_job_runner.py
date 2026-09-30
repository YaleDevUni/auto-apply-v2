"""JobRunner 큐 소비·동시성·재시도 (§A9). memory·sqlite 둘 다로 돈다."""

import asyncio

import pytest

from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.errors import (
    AuthRequired,
    BrowserLaunchFailed,
    CaptchaEncountered,
    RetryPolicy,
    SubmitGuardUnavailable,
    SubmitIncident,
)
from auto_apply.ports.jobs import JobKind, JobRecord, JobStatus
from tests.runner.kit import T0, Rig, wait_until


async def _done(rig: Rig, *job_ids: str, status: JobStatus = JobStatus.DONE) -> None:
    async def all_done() -> bool:
        return [(await rig.job(j)).status for j in job_ids] == [status] * len(job_ids)

    await wait_until(all_done)


async def test_start_and_stop_are_idempotent_and_restartable(uow_factory):
    runner = Rig(uow_factory).runner()
    assert runner.running is False
    await runner.start()
    await runner.start()
    assert runner.running is True
    await runner.stop()
    await runner.stop()
    assert runner.running is False
    await runner.start()
    assert runner.running is True
    await runner.stop()


async def test_browser_jobs_run_one_at_a_time_and_others_in_parallel(uow_factory):
    rig = Rig(uow_factory)
    active = {"browser": 0, "max_browser": 0, "other_while_browser": 0}
    browser_in, release = asyncio.Event(), asyncio.Event()

    async def browser(job: JobRecord) -> None:
        active["browser"] += 1
        active["max_browser"] = max(active["max_browser"], active["browser"])
        browser_in.set()
        await release.wait()  # 첫 브라우저 작업은 별도 슬롯 작업이 돌 때까지 붙잡힌다
        await asyncio.sleep(0.02)
        active["browser"] -= 1

    async def other(job: JobRecord) -> None:
        if active["browser"]:
            active["other_while_browser"] += 1
        release.set()

    runner = rig.runner({JobKind.FILL: browser, JobKind.SUBMIT: browser, JobKind.GENERATE: other})
    await runner.start()
    try:
        jobs = await asyncio.gather(
            runner.enqueue(JobKind.FILL),
            runner.enqueue(JobKind.SUBMIT),
            runner.enqueue(JobKind.FILL),
        )
        await asyncio.wait_for(browser_in.wait(), timeout=5)
        gen = await runner.enqueue(JobKind.GENERATE)
        await _done(rig, *(j.job_id for j in jobs), gen.job_id)
    finally:
        await runner.stop()
    assert active["max_browser"] == 1
    assert active["other_while_browser"] == 1  # 브라우저 작업이 도는 중에도 별도 슬롯은 돈다


async def test_unregistered_kind_fails_job_and_application(uow_factory):
    rig = Rig(uow_factory)
    app = await rig.app_in(S.QUEUED)
    runner = rig.runner({})
    await runner.start()
    try:
        job = await runner.enqueue(JobKind.FILL, application_id=app)
        await _done(rig, job.job_id, status=JobStatus.FAILED)
    finally:
        await runner.stop()
    assert "핸들러가 없다" in ((await rig.job(job.job_id)).error or "")
    assert await rig.state(app) is S.FAILED


async def test_transient_failure_requeues_application_and_retries(uow_factory):
    rig = Rig(uow_factory)
    app = await rig.app_in(S.QUEUED)
    calls: list[int] = []

    async def flaky(job: JobRecord) -> None:
        calls.append(job.attempt)
        if job.attempt == 1:
            raise BrowserLaunchFailed("크롬이 죽었다")
        await rig.apps.transition(app, S.AWAITING_APPROVAL, run_id="run")

    runner = rig.runner({JobKind.FILL: rig.to_filling(flaky)})
    await runner.start()
    try:
        job = await runner.enqueue(JobKind.FILL, application_id=app)
        await _done(rig, job.job_id)
    finally:
        await runner.stop()
    assert calls == [1, 2]
    assert (await rig.job(job.job_id)).attempt == 2
    async with rig.uow() as u:
        history = [h.state for h in await u.applications.history(app)]
    assert history[-4:] == [S.FILLING, S.QUEUED, S.FILLING, S.AWAITING_APPROVAL]


async def test_backoff_holds_the_job_until_run_after(uow_factory):
    rig = Rig(uow_factory, retry=RetryPolicy(max_attempts=3, base_delay_s=30))
    calls: list[int] = []

    async def fail_once(job: JobRecord) -> None:
        calls.append(job.attempt)
        if job.attempt == 1:
            raise BrowserLaunchFailed("x")

    runner = rig.runner({JobKind.REFLECT: fail_once})
    await runner.start()
    try:
        job = await runner.enqueue(JobKind.REFLECT)

        async def requeued() -> bool:
            return (await rig.job(job.job_id)).attempt == 2

        await wait_until(requeued)
        queued = await rig.job(job.job_id)
        assert queued.status is JobStatus.QUEUED
        assert queued.run_after.timestamp() == T0.timestamp() + 30
        await asyncio.sleep(0.05)
        assert calls == [1]  # 시계가 run_after 전이라 꺼내지 않는다
        rig.clock.advance(30)
        await _done(rig, job.job_id)
    finally:
        await runner.stop()
    assert calls == [1, 2]


async def test_retry_limit_fails_job_and_application(uow_factory):
    rig = Rig(uow_factory, retry=RetryPolicy(max_attempts=2, base_delay_s=0))
    app = await rig.app_in(S.QUEUED)
    calls: list[int] = []

    async def always(job: JobRecord) -> None:
        calls.append(job.attempt)
        raise BrowserLaunchFailed("x")

    runner = rig.runner({JobKind.FILL: rig.to_filling(always)})
    await runner.start()
    try:
        job = await runner.enqueue(JobKind.FILL, application_id=app)
        await _done(rig, job.job_id, status=JobStatus.FAILED)
    finally:
        await runner.stop()
    assert calls == [1, 2]
    assert await rig.state(app) is S.FAILED


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (AuthRequired("세션 만료"), S.NEEDS_LOGIN),
        (CaptchaEncountered("캡차"), S.NEEDS_INPUT),
        (SubmitIncident("완료 화면"), S.INCIDENT),
        (SubmitGuardUnavailable("가드 설치 실패"), S.FAILED),
        (RuntimeError("모르는 예외"), S.FAILED),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, Exception) else str(v),
)
async def test_domain_failures_transition_without_retry(uow_factory, exc, expected):
    rig = Rig(uow_factory)
    app = await rig.app_in(S.QUEUED)
    calls: list[int] = []

    async def boom(job: JobRecord) -> None:
        calls.append(job.attempt)
        raise exc

    runner = rig.runner({JobKind.FILL: rig.to_filling(boom)})
    await runner.start()
    try:
        job = await runner.enqueue(JobKind.FILL, application_id=app)
        await _done(rig, job.job_id, status=JobStatus.FAILED)
    finally:
        await runner.stop()
    assert calls == [1]
    assert await rig.state(app) is expected


async def test_submit_is_never_retried_and_unknown_outcome_is_incident(uow_factory):
    """제출 중 인프라 실패도 되풀이하지 않는다 — 클릭이 나갔는지 모르니 INCIDENT (이중 제출)."""
    rig = Rig(uow_factory)
    app = await rig.app_in(S.QUEUED, S.FILLING, S.AWAITING_APPROVAL)
    calls: list[int] = []

    async def submit(job: JobRecord) -> None:
        calls.append(job.attempt)
        await rig.apps.transition(app, S.SUBMITTING, run_id="run")
        raise BrowserLaunchFailed("크롬이 죽었다")

    runner = rig.runner({JobKind.SUBMIT: submit})
    await runner.start()
    try:
        job = await runner.enqueue(JobKind.SUBMIT, application_id=app)
        await _done(rig, job.job_id, status=JobStatus.FAILED)
        await asyncio.sleep(0.05)
    finally:
        await runner.stop()
    assert calls == [1]
    assert await rig.state(app) is S.INCIDENT
    assert await rig.jobs(JobStatus.QUEUED) == []


async def test_cancelled_application_is_not_retried(uow_factory):
    """사람이 취소한 뒤 핸들러가 인프라 실패로 끝나도 다시 줄 세우지 않는다."""
    rig = Rig(uow_factory)
    app = await rig.app_in(S.QUEUED)

    async def cancel_then_crash(job: JobRecord) -> None:
        await rig.apps.transition(app, S.CANCELLED, run_id=None, reason="사람이 취소")
        raise BrowserLaunchFailed("x")

    runner = rig.runner({JobKind.FILL: rig.to_filling(cancel_then_crash)})
    await runner.start()
    try:
        job = await runner.enqueue(JobKind.FILL, application_id=app)
        await _done(rig, job.job_id, status=JobStatus.FAILED)
    finally:
        await runner.stop()
    assert await rig.state(app) is S.CANCELLED
    assert (await rig.job(job.job_id)).attempt == 1
