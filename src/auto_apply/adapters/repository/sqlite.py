"""SQLite repository (D3). 스키마는 Alembic 이 만든다 — 이 모듈은 테이블을 생성하지 않는다.

단일 프로세스(§A1)라 쓰기 경합은 드물지만, API 요청과 JobRunner 가 같은 파일을 여니 잠금 대기
시간을 넉넉히 준다.
"""

from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

from sqlalchemy import event, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from auto_apply.adapters.repository.models import ApplicationRow, ApplicationStateRow
from auto_apply.contracts.dto import ApplicationSummary, PersistState
from auto_apply.domain.enums import ApplicationState

SessionFactory = async_sessionmaker[AsyncSession]

_LOCK_TIMEOUT_S = 30


def _summary(state: PersistState) -> ApplicationSummary:
    return ApplicationSummary(
        application_id=state.application_id,
        state=state.state,
        reason=state.reason,
        scheduled_at=state.scheduled_at,
        submitted_at=state.submitted_at,
    )


class SqliteApplicationRepository:
    # ORM 엔티티 대신 컬럼만 select 한다 — Core insert 뒤에 identity map 의 낡은 객체를 읽지 않게.
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_state(self, state: PersistState) -> None:
        payload = state.model_dump(mode="json")
        # FK 때문에 지원 건 행이 먼저 있어야 한다. 스냅샷 값은 아래에서 이 전이로 덮어쓴다.
        await self._session.execute(
            sqlite_insert(ApplicationRow)
            .values(id=state.application_id, state=str(state.state), payload=payload)
            .on_conflict_do_nothing(index_elements=[ApplicationRow.id])
        )
        event_id = await self._session.scalar(
            insert(ApplicationStateRow)
            .values(
                application_id=state.application_id,
                run_id=state.workflow_run_id,
                state=str(state.state),
                payload=payload,
            )
            .returning(ApplicationStateRow.id)
        )
        # append-only 라 방금 넣은 행이 곧 최신이다 (같은 run 안의 A→B→A 도 A 가 최신).
        await self._session.execute(
            update(ApplicationRow)
            .where(ApplicationRow.id == state.application_id)
            .values(
                state=str(state.state),
                payload=payload,
                last_event_id=event_id,
            )
        )

    async def history(self, application_id: str) -> list[PersistState]:
        payloads = await self._session.scalars(
            select(ApplicationStateRow.payload)
            .where(ApplicationStateRow.application_id == application_id)
            .order_by(ApplicationStateRow.id)
        )
        return [PersistState.model_validate(p) for p in payloads]

    async def list_recent(self, limit: int = 10) -> list[ApplicationSummary]:
        payloads = await self._session.scalars(
            select(ApplicationRow.payload)
            .order_by(ApplicationRow.last_event_id.desc())
            .limit(limit)
        )
        return [_summary(PersistState.model_validate(p)) for p in payloads]

    async def latest_states(self, application_ids: list[str]) -> dict[str, ApplicationState]:
        if not application_ids:
            return {}
        rows = await self._session.execute(
            select(ApplicationRow.id, ApplicationRow.state).where(
                ApplicationRow.id.in_(application_ids)
            )
        )
        return {r.id: ApplicationState(r.state) for r in rows}


class SqliteUnitOfWork:
    """세션 하나 = 트랜잭션 하나. `commit()` 없이 빠져나가면 롤백된다."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session: AsyncSession = session_factory()
        self.applications = SqliteApplicationRepository(self._session)

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


def _enable_foreign_keys(dbapi_conn: Any, _record: Any) -> None:
    # SQLite 는 연결마다 FK 강제를 켜야 한다 (기본 off).
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def build_engine(database_url: str) -> AsyncEngine:
    engine = create_async_engine(database_url, connect_args={"timeout": _LOCK_TIMEOUT_S})
    event.listen(engine.sync_engine, "connect", _enable_foreign_keys)
    return engine


def sqlite_uow_factory(database_url: str) -> Callable[[], SqliteUnitOfWork]:
    """엔진은 프로세스당 하나 — 매 UoW 마다 연결 풀을 새로 만들지 않는다."""
    session_factory = async_sessionmaker(build_engine(database_url), expire_on_commit=False)
    return lambda: SqliteUnitOfWork(session_factory)
