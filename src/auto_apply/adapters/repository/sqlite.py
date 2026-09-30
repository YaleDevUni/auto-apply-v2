"""SQLite repository (D3). 스키마는 Alembic 이 만든다 — 이 모듈은 테이블을 생성하지 않는다.

단일 프로세스(§A1)라 쓰기 경합은 드물지만, API 요청과 JobRunner 가 같은 파일을 여니 잠금 대기
시간을 넉넉히 준다.
"""

from collections.abc import Callable, Collection
from types import TracebackType
from typing import Any, Self

from sqlalchemy import event, insert, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from auto_apply.adapters.repository.models import ApplicationRow, ApplicationStateRow
from auto_apply.adapters.repository.sqlite_jobs import SqliteJobRepository
from auto_apply.adapters.repository.sqlite_profile import (
    SqliteAnswerRepository,
    SqliteDocumentRepository,
    SqliteExperienceRepository,
    SqliteProfileRepository,
)
from auto_apply.adapters.repository.sqlite_runs import SqliteRunRepository
from auto_apply.contracts.dto import ApplicationRecord, ApplicationSummary, PersistState
from auto_apply.domain.enums import ApplicationState, SubmitMode
from auto_apply.domain.errors import InvalidInput, InvalidTransition, NotFound

SessionFactory = async_sessionmaker[AsyncSession]

_LOCK_TIMEOUT_S = 30


def _summary(state: PersistState) -> ApplicationSummary:
    return ApplicationSummary(
        application_id=state.application_id,
        state=state.state,
        reason=state.reason,
        at=state.at,
        submitted_at=state.submitted_at,
    )


class SqliteApplicationRepository:
    # ORM 엔티티 대신 컬럼만 select 한다 — Core insert 뒤에 identity map 의 낡은 객체를 읽지 않게.
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: ApplicationRecord, initial: PersistState) -> None:
        payload = initial.model_dump(mode="json")
        # pysqlite 의 SAVEPOINT 는 믿기 어려워 IntegrityError 를 잡는 대신 먼저 본다
        # (단일 프로세스, §A1).
        if await self.get(record.application_id) is not None:
            raise InvalidInput(f"이미 있는 지원 건: {record.application_id}")
        await self._session.execute(
            insert(ApplicationRow).values(
                id=record.application_id,
                state=str(initial.state),
                payload=payload,
                url=record.url,
                domain=record.domain,
                submit_mode=str(record.submit_mode),
            )
        )
        await self._append_history(initial, payload)

    async def append_state(self, state: PersistState, *, expected: ApplicationState) -> None:
        payload = state.model_dump(mode="json")
        # 조건부 UPDATE 가 비교와 쓰기를 한 문장으로 한다 — 읽고 쓰는 사이에 다른 UoW 가
        # 끼지 못한다.
        res = await self._session.execute(
            update(ApplicationRow)
            .where(ApplicationRow.id == state.application_id, ApplicationRow.state == str(expected))
            .values(state=str(state.state), payload=payload)
        )
        if res.rowcount == 0:  # type: ignore[attr-defined]
            current = await self._session.scalar(
                select(ApplicationRow.state).where(ApplicationRow.id == state.application_id)
            )
            if current is None:
                raise NotFound(state.application_id)
            raise InvalidTransition(current, state.state, f"expected {expected}")
        await self._append_history(state, payload)

    async def _append_history(self, state: PersistState, payload: dict[str, Any]) -> None:
        event_id = await self._session.scalar(
            insert(ApplicationStateRow)
            .values(
                application_id=state.application_id,
                run_id=state.run_id,
                state=str(state.state),
                payload=payload,
            )
            .returning(ApplicationStateRow.id)
        )
        # append-only 라 방금 넣은 행이 곧 최신이다 (같은 run 안의 A→B→A 도 A 가 최신).
        await self._session.execute(
            update(ApplicationRow)
            .where(ApplicationRow.id == state.application_id)
            .values(last_event_id=event_id)
        )

    async def get(self, application_id: str) -> ApplicationRecord | None:
        row = (
            await self._session.execute(
                select(
                    ApplicationRow.id,
                    ApplicationRow.url,
                    ApplicationRow.domain,
                    ApplicationRow.submit_mode,
                ).where(ApplicationRow.id == application_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return ApplicationRecord(
            application_id=row.id,
            url=row.url,
            domain=row.domain,
            submit_mode=SubmitMode(row.submit_mode),
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

    async def in_states(self, states: Collection[ApplicationState]) -> dict[str, ApplicationState]:
        rows = await self._session.execute(
            select(ApplicationRow.id, ApplicationRow.state).where(
                ApplicationRow.state.in_([str(s) for s in states])
            )
        )
        return {r.id: ApplicationState(r.state) for r in rows}


class SqliteUnitOfWork:
    """세션 하나 = 트랜잭션 하나. `commit()` 없이 빠져나가면 롤백된다."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session: AsyncSession = session_factory()
        self.applications = SqliteApplicationRepository(self._session)
        self.profiles = SqliteProfileRepository(self._session)
        self.experiences = SqliteExperienceRepository(self._session)
        self.answers = SqliteAnswerRepository(self._session)
        self.documents = SqliteDocumentRepository(self._session)
        self.jobs = SqliteJobRepository(self._session)
        self.runs = SqliteRunRepository(self._session)

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


def _on_connect(dbapi_conn: Any, _record: Any) -> None:
    # SQLite 는 연결마다 FK 강제를 켜야 한다 (기본 off).
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    # 드라이버의 암묵 BEGIN 을 끄고 `_begin_immediate` 가 트랜잭션을 연다.
    dbapi_conn.isolation_level = None


def _begin_immediate(conn: Any) -> None:
    # 읽고 나서 쓰는 트랜잭션(claim·transition) 둘이 겹치면 DEFERRED 는 쓰기 잠금 승격에서
    # 교착을 감지하고 기다리지 않고 "database is locked" 로 실패한다. 처음부터 쓰기 잠금을
    # 잡으면 뒤에 온 쪽은 timeout 까지 기다린다 — API 와 JobRunner 가 한 파일을 같이 쓴다(§A1).
    conn.exec_driver_sql("BEGIN IMMEDIATE")


def build_engine(database_url: str) -> AsyncEngine:
    engine = create_async_engine(database_url, connect_args={"timeout": _LOCK_TIMEOUT_S})
    event.listen(engine.sync_engine, "connect", _on_connect)
    event.listen(engine.sync_engine, "begin", _begin_immediate)
    return engine


def sqlite_uow_factory(database_url: str) -> Callable[[], SqliteUnitOfWork]:
    """엔진은 프로세스당 하나 — 매 UoW 마다 연결 풀을 새로 만들지 않는다."""
    session_factory = async_sessionmaker(build_engine(database_url), expire_on_commit=False)
    return lambda: SqliteUnitOfWork(session_factory)
