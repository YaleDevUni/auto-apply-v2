"""SQLAlchemy(Postgres) repository. `FileUnitOfWork`와 같은 계약, 저장소만 다르다.

M1 은 파일로 버텼지만(`adapters/repository/file.py` 상단 주석), 워커 3개가 같은 파일을
동시에 잠그는 것보다 Postgres 의 `INSERT ... ON CONFLICT`가 멱등 upsert(§4.1)를 훨씬
안전하게 보장한다(§9.1). Alembic 마이그레이션은 `alembic/versions/`.
"""

from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from auto_apply.adapters.repository.models import (
    ApplicationAttemptRow,
    ApplicationStateRow,
    JobRow,
    ScheduleConfigRow,
)
from auto_apply.contracts.dto import (
    ApplicationAttempt,
    ApplicationSummary,
    PersistState,
    ScheduleConfig,
)
from auto_apply.contracts.job import JobRecord
from auto_apply.domain.enums import ApplicationState

SessionFactory = async_sessionmaker[AsyncSession]


def _summary(state: PersistState) -> ApplicationSummary:
    return ApplicationSummary(
        application_id=state.application_id,
        state=state.state,
        reason=state.reason,
        scheduled_at=state.scheduled_at,
        submitted_at=state.submitted_at,
    )


class SqlAlchemyApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_state(self, state: PersistState) -> None:
        stmt = pg_insert(ApplicationStateRow).values(
            application_id=state.application_id,
            workflow_run_id=state.workflow_run_id,
            state=str(state.state),
            payload=state.model_dump(mode="json"),
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_application_state",
            set_={"payload": stmt.excluded.payload},
        )
        await self._session.execute(stmt)

    async def history(self, application_id: str) -> list[PersistState]:
        rows = await self._session.scalars(
            select(ApplicationStateRow)
            .where(ApplicationStateRow.application_id == application_id)
            .order_by(ApplicationStateRow.id)
        )
        return [PersistState.model_validate(r.payload) for r in rows]

    async def list_recent(self, limit: int = 10) -> list[ApplicationSummary]:
        # 새 컬럼 없이 기존 autoincrement id 를 "최근성"으로 쓴다 — application_id 별 최신 id 를
        # 서브쿼리로 구해 그 row 들만 id 내림차순으로 가져온다.
        latest_id = (
            select(func.max(ApplicationStateRow.id))
            .group_by(ApplicationStateRow.application_id)
            .scalar_subquery()
        )
        rows = await self._session.scalars(
            select(ApplicationStateRow)
            .where(ApplicationStateRow.id.in_(latest_id))
            .order_by(ApplicationStateRow.id.desc())
            .limit(limit)
        )
        return [_summary(PersistState.model_validate(r.payload)) for r in rows]

    async def latest_states(self, application_ids: list[str]) -> dict[str, ApplicationState]:
        if not application_ids:
            return {}
        latest_id = (
            select(func.max(ApplicationStateRow.id))
            .where(ApplicationStateRow.application_id.in_(application_ids))
            .group_by(ApplicationStateRow.application_id)
            .scalar_subquery()
        )
        rows = await self._session.scalars(
            select(ApplicationStateRow).where(ApplicationStateRow.id.in_(latest_id))
        )
        return {r.application_id: PersistState.model_validate(r.payload).state for r in rows}


class SqlAlchemyJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, record: JobRecord) -> None:
        actionable = record.applicability.actionable if record.applicability else None
        stmt = pg_insert(JobRow).values(
            platform=record.job.platform,
            platform_job_id=record.job.platform_job_id,
            actionable=actionable,
            payload=record.model_dump(mode="json"),
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_job_platform_id",
            set_={"actionable": stmt.excluded.actionable, "payload": stmt.excluded.payload},
        )
        await self._session.execute(stmt)

    async def get(self, platform: str, platform_job_id: str) -> JobRecord | None:
        row = await self._session.scalar(
            select(JobRow).where(
                JobRow.platform == platform, JobRow.platform_job_id == platform_job_id
            )
        )
        return JobRecord.model_validate(row.payload) if row else None

    async def actionable(self) -> list[JobRecord]:
        rows = await self._session.scalars(select(JobRow).where(JobRow.actionable.is_(True)))
        return [JobRecord.model_validate(r.payload) for r in rows]


class SqlAlchemyAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, attempt: ApplicationAttempt) -> None:
        stmt = pg_insert(ApplicationAttemptRow).values(
            application_id=attempt.application_id,
            attempt=attempt.attempt,
            payload=attempt.model_dump(mode="json"),
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_application_attempt",
            set_={"payload": stmt.excluded.payload},
        )
        await self._session.execute(stmt)

    async def history(self, application_id: str) -> list[ApplicationAttempt]:
        rows = await self._session.scalars(
            select(ApplicationAttemptRow)
            .where(ApplicationAttemptRow.application_id == application_id)
            .order_by(ApplicationAttemptRow.id)
        )
        return [ApplicationAttempt.model_validate(r.payload) for r in rows]


class SqlAlchemyScheduleConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, target: str) -> ScheduleConfig | None:
        row = await self._session.scalar(
            select(ScheduleConfigRow).where(ScheduleConfigRow.target == target)
        )
        return ScheduleConfig.model_validate(row.payload) if row else None

    async def set(self, config: ScheduleConfig) -> None:
        stmt = pg_insert(ScheduleConfigRow).values(
            target=config.target, payload=config.model_dump(mode="json")
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ScheduleConfigRow.target], set_={"payload": stmt.excluded.payload}
        )
        await self._session.execute(stmt)


class SqlAlchemyUnitOfWork:
    """세션 하나 = 트랜잭션 하나. `commit()`을 부르지 않으면 `__aexit__`에서 롤백된다.

    repos 는 `__init__`에서 바로 만든다 (memory/file 어댑터와 같은 모양) — `applications` 등이
    `async with` 진입 전에도 접근 가능해야 protocol 을 그대로 만족한다. 세션 생성 자체는
    커넥션을 열지 않는 가벼운 객체라 문제 없다.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session: AsyncSession = session_factory()
        self.applications = SqlAlchemyApplicationRepository(self._session)
        self.jobs = SqlAlchemyJobRepository(self._session)
        self.attempts = SqlAlchemyAttemptRepository(self._session)
        self.schedule_config = SqlAlchemyScheduleConfigRepository(self._session)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                await self._session.rollback()
        finally:
            await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()


def build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url)


def sqlalchemy_uow_factory(database_url: str) -> Callable[[], SqlAlchemyUnitOfWork]:
    """엔진은 프로세스당 하나만 만든다 (커넥션 풀을 매 요청마다 새로 열지 않는다)."""
    engine = build_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return lambda: SqlAlchemyUnitOfWork(session_factory)
