"""JobRunner — API 와 같은 프로세스에서 도는 단일 asyncio 소비자 (§A1, §A9).

브라우저 작업(fill/revise/submit)은 전용 크롬 창이 하나라 동시성 1, 그 외(생성·반성)는 별도 슬롯.
jobs 테이블이 유일한 대기열이라 정지·크래시 뒤에도 QUEUED 행은 다음 기동에 그대로 소비된다.
지원 건 상태는 `ApplicationService` 로만 바꾼다(절대 규칙 6).
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta

import structlog

from auto_apply.contracts.jobs import (
    BROWSER_KINDS,
    NEVER_RETRY_KINDS,
    JobKind,
    JobRecord,
    JobStatus,
)
from auto_apply.domain.failure import FailureKind, RetryPolicy, classify_failure
from auto_apply.domain.unique_identifiers import redact_resident_registration_numbers
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.repository import UnitOfWork
from auto_apply.runner.recovery import can_rerun, recover_interrupted
from auto_apply.services.application import ApplicationService

log = structlog.get_logger(__name__)

Handler = Callable[[JobRecord], Awaitable[None]]

_OTHER_KINDS: frozenset[JobKind] = frozenset(JobKind) - BROWSER_KINDS
_ERROR_MAX = 500


def describe_error(exc: BaseException) -> str:
    """저장·로그용 한 줄. 예외 메시지에 섞여 들어온 주민번호는 가린다(절대 규칙 5)."""
    text, _ = redact_resident_registration_numbers(f"{type(exc).__name__}: {exc}")
    return text[:_ERROR_MAX]


class JobRunner:
    def __init__(
        self,
        uow: Callable[[], UnitOfWork],
        applications: ApplicationService,
        clock: Clock,
        idgen: IdGen,
        *,
        handlers: Mapping[JobKind, Handler] | None = None,
        retry: RetryPolicy | None = None,
        other_concurrency: int = 2,
        poll_interval_s: float = 1.0,
    ) -> None:
        self._uow = uow
        self._apps = applications
        self._clock = clock
        self._idgen = idgen
        self._handlers = dict(handlers or {})
        self._retry = retry or RetryPolicy()
        self._pools = ((BROWSER_KINDS, 1), (_OTHER_KINDS, other_concurrency))
        self._poll = poll_interval_s
        self._tasks: list[asyncio.Task[None]] = []
        self._wake = asyncio.Event()

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self._tasks)

    async def enqueue(
        self,
        kind: JobKind,
        *,
        application_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> JobRecord:
        now = self._clock.now()
        job = JobRecord(
            job_id=self._idgen.new_id("job"),
            kind=kind,
            application_id=application_id,
            payload=payload or {},
            created_at=now,
            run_after=now,
        )
        async with self._uow() as uow:
            await uow.jobs.enqueue(job)
            await uow.commit()
        self._wake.set()
        log.info("job.enqueued", application_id=application_id, run_id=None, job_id=job.job_id)
        return job

    async def start(self) -> None:
        if self.running:
            return
        # 앞선 프로세스가 죽으며 남긴 RUNNING 은 전부 낡았다 — 소비를 시작하기 전에 닫는다.
        await recover_interrupted(
            self._uow, self._apps, self._clock, self._idgen, self._retry, count_attempt=True
        )
        self._wake = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self._worker(kinds), name=f"job-worker-{i}")
            for i, (kinds, size) in enumerate(self._pools)
            for _ in range(size)
        ]
        log.info("job_runner.started", application_id=None, run_id=None)

    async def stop(self) -> None:
        """진행 중 job 을 취소하고 INTERRUPTED 로 닫는다. 대기 중 job 은 다음 기동에 소비된다."""
        if not self._tasks:
            return
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # 사용자가 앱을 끈 것은 실패가 아니다 — 재시도 횟수를 쓰지 않는다.
        await recover_interrupted(
            self._uow, self._apps, self._clock, self._idgen, self._retry, count_attempt=False
        )
        log.info("job_runner.stopped", application_id=None, run_id=None)

    async def _worker(self, kinds: frozenset[JobKind]) -> None:
        while True:
            try:
                async with self._uow() as uow:
                    job = await uow.jobs.claim_next(kinds, now=self._clock.now())
                    await uow.commit()
            except Exception as exc:
                # 저장소 일시 오류(잠금 대기 초과 등)로 소비자가 죽지 않게 — 다음 폴링에 다시 본다.
                log.error("job.claim_failed", application_id=None, run_id=None, error=str(exc))
                job = None
            if job is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self._poll)
                self._wake.clear()
                continue
            await self._execute(job)

    async def _execute(self, job: JobRecord) -> None:
        ctx = {"application_id": job.application_id, "run_id": None, "job_id": job.job_id}
        handler = self._handlers.get(job.kind)
        if handler is None:
            log.error("job.no_handler", kind=str(job.kind), **ctx)
            await self._fail(job, FailureKind.FATAL, f"'{job.kind}' 핸들러가 없다")
            return
        log.info("job.started", kind=str(job.kind), attempt=job.attempt, **ctx)
        try:
            await handler(job)
        except Exception as exc:
            await self._on_error(job, exc)
            return
        async with self._uow() as uow:
            await uow.jobs.finish(job.job_id, JobStatus.DONE, at=self._clock.now())
            await uow.commit()
        log.info("job.done", **ctx)

    async def _on_error(self, job: JobRecord, exc: Exception) -> None:
        failure = classify_failure(exc)
        error = describe_error(exc)
        ctx = {"application_id": job.application_id, "run_id": None, "job_id": job.job_id}
        if (
            failure is FailureKind.TRANSIENT
            and job.kind not in NEVER_RETRY_KINDS
            and self._retry.should_retry(job.attempt)
        ):
            if job.application_id is not None:
                await self._apps.settle_failure(
                    job.application_id, failure, run_id=None, reason=error, retry=True
                )
            if await can_rerun(self._apps, job):
                delay = self._retry.delay_s(job.attempt)
                async with self._uow() as uow:
                    await uow.jobs.requeue(
                        job.job_id,
                        attempt=job.attempt + 1,
                        run_after=self._clock.now() + timedelta(seconds=delay),
                        error=error,
                    )
                    await uow.commit()
                log.warning("job.retry_scheduled", delay_s=delay, error=error, **ctx)
                return
        log.warning("job.failed", failure=str(failure), error=error, **ctx)
        await self._fail(job, failure, error)

    async def _fail(self, job: JobRecord, failure: FailureKind, error: str) -> None:
        if job.application_id is not None:
            await self._apps.settle_failure(job.application_id, failure, run_id=None, reason=error)
        async with self._uow() as uow:
            await uow.jobs.finish(job.job_id, JobStatus.FAILED, at=self._clock.now(), error=error)
            await uow.commit()
