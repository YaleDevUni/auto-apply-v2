from types import TracebackType
from typing import Self

from auto_apply.contracts.dto import (
    ApplicationAttempt,
    ApplicationSummary,
    CachedResume,
    PersistState,
    ScheduleConfig,
)
from auto_apply.contracts.job import JobRecord
from auto_apply.domain.enums import ApplicationState

Rows = dict[str, list[PersistState]]
JobRows = dict[tuple[str, str], JobRecord]
AttemptRows = dict[str, list[ApplicationAttempt]]
ScheduleConfigRows = dict[str, ScheduleConfig]
ResumeRows = dict[str, CachedResume]


def _summary(application_id: str, latest: PersistState) -> ApplicationSummary:
    return ApplicationSummary(
        application_id=application_id,
        state=latest.state,
        reason=latest.reason,
        scheduled_at=latest.scheduled_at,
        submitted_at=latest.submitted_at,
    )


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

    async def list_recent(self, limit: int = 10) -> list[ApplicationSummary]:
        # 정렬 순서는 보장하지 않는다(테스트 대역, ports/repository.py 참고) — dict 삽입 순서를
        # 최신순처럼 뒤집어 보여줄 뿐이다.
        app_ids = list(reversed(self._rows))[:limit]
        return [
            _summary(app_id, self._rows[app_id][-1]) for app_id in app_ids if self._rows[app_id]
        ]

    async def latest_states(self, application_ids: list[str]) -> dict[str, ApplicationState]:
        return {aid: self._rows[aid][-1].state for aid in application_ids if self._rows.get(aid)}


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


class InMemoryScheduleConfigRepository:
    def __init__(self, rows: ScheduleConfigRows) -> None:
        self._rows = rows

    async def get(self, target: str) -> ScheduleConfig | None:
        return self._rows.get(target)

    async def set(self, config: ScheduleConfig) -> None:
        self._rows[config.target] = config


class InMemoryResumeRepository:
    def __init__(self, rows: ResumeRows) -> None:
        self._rows = rows

    async def get(self, application_id: str) -> CachedResume | None:
        return self._rows.get(application_id)

    async def save(self, resume: CachedResume) -> None:
        self._rows[resume.application_id] = resume


class InMemoryUnitOfWork:
    def __init__(
        self,
        rows: Rows,
        job_rows: JobRows | None = None,
        attempt_rows: AttemptRows | None = None,
        schedule_config_rows: ScheduleConfigRows | None = None,
        resume_rows: ResumeRows | None = None,
    ) -> None:
        self.applications = InMemoryApplicationRepository(rows)
        self.jobs = InMemoryJobRepository(job_rows if job_rows is not None else {})
        self.attempts = InMemoryAttemptRepository(attempt_rows if attempt_rows is not None else {})
        self.schedule_config = InMemoryScheduleConfigRepository(
            schedule_config_rows if schedule_config_rows is not None else {}
        )
        self.resumes = InMemoryResumeRepository(resume_rows if resume_rows is not None else {})

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
