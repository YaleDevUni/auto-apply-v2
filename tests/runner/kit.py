"""JobRunner 테스트 공용 — 손으로 돌리는 시계, 조립, 조건 대기."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta

from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.errors import RetryPolicy
from auto_apply.ports.jobs import JobKind, JobRecord, JobStatus
from auto_apply.runner.job_runner import Handler, JobRunner
from auto_apply.services.application import ApplicationService
from tests.services.fakes import SeqIds

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


class ManualClock:
    def __init__(self) -> None:
        self.t = T0

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class Rig:
    def __init__(self, uow_factory, *, retry: RetryPolicy | None = None) -> None:
        self.uow = uow_factory
        self.clock = ManualClock()
        self.ids = SeqIds()
        self.apps = ApplicationService(uow_factory, self.clock, self.ids)
        self.retry = retry or RetryPolicy(max_attempts=3, base_delay_s=0)

    def runner(self, handlers: Mapping[JobKind, Handler] | None = None) -> JobRunner:
        return JobRunner(
            self.uow,
            self.apps,
            self.clock,
            self.ids,
            handlers=handlers,
            retry=self.retry,
            poll_interval_s=0.01,
        )

    async def app_in(self, *path: S) -> str:
        record = await self.apps.create("https://jobs.example.com/p/1")
        for s in path:
            await self.apps.transition(record.application_id, s, run_id=None)
        return record.application_id

    async def job(self, job_id: str) -> JobRecord:
        async with self.uow() as u:
            job = await u.jobs.get(job_id)
        assert job is not None
        return job

    async def jobs(self, status: JobStatus) -> list[JobRecord]:
        async with self.uow() as u:
            return await u.jobs.list_by_status(status)

    async def state(self, application_id: str) -> S:
        return await self.apps.current_state(application_id)

    def to_filling(self, then: Callable[[JobRecord], Awaitable[None]] | None = None) -> Handler:
        """fill 핸들러 흉내: QUEUED→FILLING 한 뒤 `then`(없으면 AWAITING_APPROVAL)."""

        async def handler(job: JobRecord) -> None:
            assert job.application_id is not None
            await self.apps.transition(job.application_id, S.FILLING, run_id="run")
            if then is not None:
                await then(job)
            else:
                await self.apps.transition(job.application_id, S.AWAITING_APPROVAL, run_id="run")

        return handler


async def wait_until(pred: Callable[[], Awaitable[bool]], *, polls: int = 500) -> None:
    """저장소 상태를 10ms 간격으로 본다(기본 5초). 러너는 알림을 주지 않아 폴링한다."""
    for _ in range(polls):
        if await pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("조건이 제한 시간 안에 참이 되지 않았다")
