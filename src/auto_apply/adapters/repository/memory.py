from types import TracebackType
from typing import Self

from auto_apply.contracts.dto import ApplicationAttempt, PersistState
from auto_apply.contracts.job import JobRecord

Rows = dict[str, list[PersistState]]
JobRows = dict[tuple[str, str], JobRecord]
AttemptRows = dict[str, list[ApplicationAttempt]]


class InMemoryApplicationRepository:
    def __init__(self, rows: Rows) -> None:
        self._rows = rows

    async def upsert_state(self, state: PersistState) -> None:
        history = self._rows.setdefault(state.application_id, [])
        # 멱등: 같은 (run_id, state) 가 이미 있으면 값만 갱신한다
        for i, existing in enumerate(history):
            if existing.workflow_run_id == state.workflow_run_id and existing.state == state.state:
                history[i] = state
                return
        history.append(state)

    async def history(self, application_id: str) -> list[PersistState]:
        return list(self._rows.get(application_id, []))


class InMemoryJobRepository:
    def __init__(self, rows: JobRows) -> None:
        self._rows = rows

    async def upsert(self, record: JobRecord) -> None:
        self._rows[(record.job.platform, record.job.platform_job_id)] = record

    async def get(self, platform: str, platform_job_id: str) -> JobRecord | None:
        return self._rows.get((platform, platform_job_id))

    async def actionable(self) -> list[JobRecord]:
        return [r for r in self._rows.values() if r.applicability and r.applicability.actionable]


class InMemoryAttemptRepository:
    def __init__(self, rows: AttemptRows) -> None:
        self._rows = rows

    async def record(self, attempt: ApplicationAttempt) -> None:
        history = self._rows.setdefault(attempt.application_id, [])
        for i, existing in enumerate(history):
            if existing.attempt == attempt.attempt:
                history[i] = attempt
                return
        history.append(attempt)

    async def history(self, application_id: str) -> list[ApplicationAttempt]:
        return list(self._rows.get(application_id, []))


class InMemoryUnitOfWork:
    def __init__(
        self,
        rows: Rows,
        job_rows: JobRows | None = None,
        attempt_rows: AttemptRows | None = None,
    ) -> None:
        self.applications = InMemoryApplicationRepository(rows)
        self.jobs = InMemoryJobRepository(job_rows if job_rows is not None else {})
        self.attempts = InMemoryAttemptRepository(attempt_rows if attempt_rows is not None else {})

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None


def in_memory_uow(rows: Rows | None = None) -> tuple[Rows, "type[InMemoryUnitOfWork]"]:
    """테스트용 헬퍼. rows 를 공유하는 UoW 팩토리를 만든다."""
    shared: Rows = rows if rows is not None else {}
    return shared, InMemoryUnitOfWork
