"""메모리 작업 큐·run 기록 — 테스트 대역 (§A2 구현 2개, ports/jobs.py · DTO contracts/jobs.py).

asyncio 단일 스레드라 한 메서드 안에 await 가 없으면 원자적이다 — claim 이 겹치지 않는다.
"""

from datetime import datetime

from auto_apply.contracts._base import ensure_identifier_free
from auto_apply.contracts.jobs import JobKind, JobRecord, JobStatus, RunRecord
from auto_apply.domain.enums import RunStatus
from auto_apply.domain.errors import InvalidInput, NotFound
from auto_apply.domain.unique_identifiers import reject_unique_identifiers


class InMemoryJobRepository:
    def __init__(self, rows: dict[str, JobRecord]) -> None:
        self._rows = rows

    async def enqueue(self, job: JobRecord) -> None:
        ensure_identifier_free(job)
        if job.job_id in self._rows:
            raise InvalidInput(f"이미 있는 job: {job.job_id}")
        self._rows[job.job_id] = job.model_copy(update={"status": JobStatus.QUEUED})

    async def claim_next(self, kinds: frozenset[JobKind], *, now: datetime) -> JobRecord | None:
        ready = [
            j
            for j in self._rows.values()
            if j.status is JobStatus.QUEUED and j.kind in kinds and j.run_after <= now
        ]
        if not ready:
            return None
        job = min(ready, key=lambda j: (j.run_after, j.created_at, j.job_id))
        claimed = job.model_copy(update={"status": JobStatus.RUNNING, "started_at": now})
        self._rows[job.job_id] = claimed
        return claimed

    def _running(self, job_id: str) -> JobRecord:
        job = self._rows.get(job_id)
        if job is None:
            raise NotFound(job_id)
        if job.status is not JobStatus.RUNNING:
            raise InvalidInput(f"실행 중이 아닌 job: {job_id}")
        return job

    async def finish(
        self, job_id: str, status: JobStatus, *, at: datetime, error: str | None = None
    ) -> None:
        if status not in {JobStatus.DONE, JobStatus.FAILED}:
            raise InvalidInput(f"job 종료 상태가 아니다: {status}")
        if error is not None:
            reject_unique_identifiers(error, where="job.error")
        job = self._running(job_id)
        self._rows[job_id] = job.model_copy(
            update={"status": status, "finished_at": at, "error": error}
        )

    async def requeue(self, job_id: str, *, attempt: int, run_after: datetime, error: str) -> None:
        reject_unique_identifiers(error, where="job.error")
        job = self._running(job_id)
        self._rows[job_id] = job.model_copy(
            update={
                "status": JobStatus.QUEUED,
                "attempt": attempt,
                "run_after": run_after,
                "started_at": None,
                "error": error,
            }
        )

    async def interrupt_running(self, *, at: datetime) -> list[JobRecord]:
        running = await self.list_by_status(JobStatus.RUNNING)
        for job in running:
            self._rows[job.job_id] = job.model_copy(
                update={"status": JobStatus.INTERRUPTED, "finished_at": at}
            )
        return running

    async def get(self, job_id: str) -> JobRecord | None:
        return self._rows.get(job_id)

    async def list_by_status(self, status: JobStatus) -> list[JobRecord]:
        found = [j for j in self._rows.values() if j.status is status]
        return sorted(found, key=lambda j: (j.created_at, j.job_id))


class InMemoryRunRepository:
    def __init__(self, rows: dict[str, RunRecord]) -> None:
        self._rows = rows

    async def start(self, run: RunRecord) -> None:
        ensure_identifier_free(run)
        if run.run_id in self._rows:
            raise InvalidInput(f"이미 있는 run: {run.run_id}")
        self._rows[run.run_id] = run.model_copy(update={"status": RunStatus.RUNNING})

    async def finish(
        self,
        run_id: str,
        status: RunStatus,
        *,
        at: datetime,
        result: str | None = None,
        error: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        transcript_path: str | None = None,
    ) -> None:
        if status is RunStatus.RUNNING:
            raise InvalidInput("run 을 RUNNING 으로 닫을 수 없다")
        if error is not None:
            reject_unique_identifiers(error, where="run.error")
        run = self._rows.get(run_id)
        if run is None:
            raise NotFound(run_id)
        if run.status is not RunStatus.RUNNING:
            raise InvalidInput(f"이미 닫힌 run: {run_id}")
        self._rows[run_id] = run.model_copy(
            update={
                "status": status,
                "finished_at": at,
                "result": result,
                "error": error,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "transcript_path": transcript_path,
            }
        )

    async def interrupt_running(self, *, at: datetime) -> list[str]:
        ids = [r.run_id for r in self._rows.values() if r.status is RunStatus.RUNNING]
        for run_id in ids:
            self._rows[run_id] = self._rows[run_id].model_copy(
                update={"status": RunStatus.INTERRUPTED, "finished_at": at}
            )
        return ids

    async def get(self, run_id: str) -> RunRecord | None:
        return self._rows.get(run_id)
