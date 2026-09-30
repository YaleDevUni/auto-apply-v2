"""SQLite 작업 큐 (§A9). 시각은 UTC naive 로 저장한다(SQLite DateTime 은 tz 를 버린다)."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply.adapters.repository.models import JobRow
from auto_apply.contracts._base import ensure_identifier_free
from auto_apply.contracts.jobs import JobKind, JobRecord, JobStatus
from auto_apply.domain.errors import InvalidInput, NotFound
from auto_apply.domain.unique_identifiers import reject_unique_identifiers


def to_db(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(tzinfo=None)


def from_db(at: datetime | None) -> datetime | None:
    return None if at is None else at.replace(tzinfo=UTC)


_JOB_COLUMNS = (
    JobRow.id,
    JobRow.kind,
    JobRow.application_id,
    JobRow.status,
    JobRow.attempt,
    JobRow.payload,
    JobRow.created_at,
    JobRow.run_after,
    JobRow.started_at,
    JobRow.finished_at,
    JobRow.error,
)


def _job(row: Row[Any]) -> JobRecord:
    return JobRecord(
        job_id=row.id,
        kind=JobKind(row.kind),
        application_id=row.application_id,
        status=JobStatus(row.status),
        attempt=row.attempt,
        payload=row.payload,
        created_at=row.created_at.replace(tzinfo=UTC),
        run_after=row.run_after.replace(tzinfo=UTC),
        started_at=from_db(row.started_at),
        finished_at=from_db(row.finished_at),
        error=row.error,
    )


class SqliteJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, job: JobRecord) -> None:
        ensure_identifier_free(job)
        if await self.get(job.job_id) is not None:
            raise InvalidInput(f"이미 있는 job: {job.job_id}")
        await self._session.execute(
            insert(JobRow).values(
                id=job.job_id,
                kind=str(job.kind),
                application_id=job.application_id,
                status=str(JobStatus.QUEUED),
                attempt=job.attempt,
                payload=job.payload,
                created_at=to_db(job.created_at),
                run_after=to_db(job.run_after),
            )
        )

    async def claim_next(self, kinds: frozenset[JobKind], *, now: datetime) -> JobRecord | None:
        job_id = await self._session.scalar(
            select(JobRow.id)
            .where(
                JobRow.status == str(JobStatus.QUEUED),
                JobRow.kind.in_([str(k) for k in kinds]),
                JobRow.run_after <= to_db(now),
            )
            .order_by(JobRow.run_after, JobRow.created_at, JobRow.id)
            .limit(1)
        )
        if job_id is None:
            return None
        # 조건부 UPDATE — 그 사이 다른 소비자가 꺼냈으면 0행이라 빈손으로 돌아간다.
        res = await self._session.execute(
            update(JobRow)
            .where(JobRow.id == job_id, JobRow.status == str(JobStatus.QUEUED))
            .values(status=str(JobStatus.RUNNING), started_at=to_db(now))
        )
        if res.rowcount == 0:  # type: ignore[attr-defined]
            return None
        return await self.get(job_id)

    async def _update_running(self, job_id: str, **values: object) -> None:
        if isinstance(err := values.get("error"), str):
            reject_unique_identifiers(err, where="job.error")
        res = await self._session.execute(
            update(JobRow)
            .where(JobRow.id == job_id, JobRow.status == str(JobStatus.RUNNING))
            .values(**values)
        )
        if res.rowcount == 0:  # type: ignore[attr-defined]
            if await self.get(job_id) is None:
                raise NotFound(job_id)
            raise InvalidInput(f"실행 중이 아닌 job: {job_id}")

    async def finish(
        self, job_id: str, status: JobStatus, *, at: datetime, error: str | None = None
    ) -> None:
        if status not in {JobStatus.DONE, JobStatus.FAILED}:
            raise InvalidInput(f"job 종료 상태가 아니다: {status}")
        await self._update_running(job_id, status=str(status), finished_at=to_db(at), error=error)

    async def requeue(self, job_id: str, *, attempt: int, run_after: datetime, error: str) -> None:
        await self._update_running(
            job_id,
            status=str(JobStatus.QUEUED),
            attempt=attempt,
            run_after=to_db(run_after),
            started_at=None,
            error=error,
        )

    async def interrupt_running(self, *, at: datetime) -> list[JobRecord]:
        running = await self.list_by_status(JobStatus.RUNNING)
        await self._session.execute(
            update(JobRow)
            .where(JobRow.status == str(JobStatus.RUNNING))
            .values(status=str(JobStatus.INTERRUPTED), finished_at=to_db(at))
        )
        return running

    async def get(self, job_id: str) -> JobRecord | None:
        row = (
            await self._session.execute(select(*_JOB_COLUMNS).where(JobRow.id == job_id))
        ).one_or_none()
        return None if row is None else _job(row)

    async def list_by_status(self, status: JobStatus) -> list[JobRecord]:
        rows = await self._session.execute(
            select(*_JOB_COLUMNS)
            .where(JobRow.status == str(status))
            .order_by(JobRow.created_at, JobRow.id)
        )
        return [_job(r) for r in rows]
