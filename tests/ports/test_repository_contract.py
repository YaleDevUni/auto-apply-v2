"""ApplicationRepository contract test — 멱등성이 계약의 핵심이다 (§4.1).

activity 는 최소 1회 실행이므로 같은 값으로 두 번 불려도 결과가 같아야 한다.
postgres 파라미터만 실제 DB 라운드트립이라 `docker`로 표시한다 — `make test`는 memory/file 만
돌고, postgres 는 `make up` 이 떠 있는 `make test-all`에서만 돈다.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from auto_apply.adapters.repository.file import FileUnitOfWork
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.adapters.repository.models import Base
from auto_apply.adapters.repository.postgres import SqlAlchemyUnitOfWork, build_engine
from auto_apply.config import Settings
from auto_apply.contracts.dto import PersistState
from auto_apply.domain.enums import ApplicationState
from auto_apply.ports.repository import UnitOfWork

_PG_TABLES = "application_state_history"


@pytest.fixture(params=["memory", "file", pytest.param("postgres", marks=pytest.mark.docker)])
async def uow_factory(request: pytest.FixtureRequest, tmp_path):
    if request.param == "memory":
        rows: dict = {}
        yield lambda: InMemoryUnitOfWork(rows)
        return
    if request.param == "file":
        yield lambda: FileUnitOfWork(tmp_path)
        return

    # postgres — 매 테스트 전에 비워서 이전 테스트의 app_1 행과 섞이지 않게 한다.
    # 반드시 운영 database_url 과 분리된 DB 를 쓴다 — 섞이면 TRUNCATE 가 실제 데이터를
    # 지운다 (postgres-integration-test-data-wipe-hazard 로 실측).
    settings = Settings()
    test_url = settings.test_database_url
    assert test_url != settings.database_url, (
        "TEST_DATABASE_URL 이 DATABASE_URL 과 같다 — 이 fixture 는 매 테스트 전에 TRUNCATE 하므로"
        " 운영 DB 를 그대로 가리키면 실제 데이터가 지워진다. db-init/01-create-test-db.sql 참고."
    )
    engine = build_engine(test_url)
    async with engine.begin() as conn:
        # alembic 을 별도로 이 DB에 돌리지 않는다 — models.py 가 유일한 스키마 정의라
        # create_all 이 alembic 마이그레이션과 항상 같은 결과를 낸다 (스키마가 갈리면 그 자체가
        # models.py 변경 시 놓친 마이그레이션이라는 신호다).
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(f"TRUNCATE TABLE {_PG_TABLES} RESTART IDENTITY"))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield lambda: SqlAlchemyUnitOfWork(session_factory)
    await engine.dispose()


def _state(state: ApplicationState, run_id: str = "run_1", **kw) -> PersistState:
    return PersistState(application_id="app_1", workflow_run_id=run_id, state=state, **kw)


async def test_upsert_then_history(uow_factory):
    async with uow_factory() as uow:
        await uow.applications.upsert_state(_state(ApplicationState.EVALUATING))
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.applications.history("app_1")
    assert [h.state for h in history] == [ApplicationState.EVALUATING]


async def test_duplicate_upsert_does_not_duplicate_rows(uow_factory):
    """activity 재시도로 같은 호출이 두 번 와도 이력이 늘어나지 않는다."""
    for _ in range(3):
        async with uow_factory() as uow:
            await uow.applications.upsert_state(_state(ApplicationState.SCHEDULED))
            await uow.commit()
    async with uow_factory() as uow:
        assert len(await uow.applications.history("app_1")) == 1


async def test_same_state_updates_values_in_place(uow_factory):
    at = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    async with uow_factory() as uow:
        await uow.applications.upsert_state(_state(ApplicationState.SCHEDULED))
        await uow.applications.upsert_state(_state(ApplicationState.SCHEDULED, scheduled_at=at))
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.applications.history("app_1")
    assert len(history) == 1
    assert history[0].scheduled_at == at


async def test_different_states_are_appended_in_order(uow_factory):
    async with uow_factory() as uow:
        for s in (
            ApplicationState.EVALUATING,
            ApplicationState.AWAITING_APPROVAL,
            ApplicationState.COMPLETED,
        ):
            await uow.applications.upsert_state(_state(s))
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.applications.history("app_1")
    assert [h.state for h in history] == [
        ApplicationState.EVALUATING,
        ApplicationState.AWAITING_APPROVAL,
        ApplicationState.COMPLETED,
    ]


async def test_retry_of_same_state_on_new_run_is_separate(uow_factory):
    """재실행(run_id 변경)은 별도 이력이다. 어느 run 이 무엇을 했는지 추적 가능해야 한다."""
    async with uow_factory() as uow:
        await uow.applications.upsert_state(_state(ApplicationState.EXECUTING, run_id="run_1"))
        await uow.applications.upsert_state(_state(ApplicationState.EXECUTING, run_id="run_2"))
        await uow.commit()
    async with uow_factory() as uow:
        assert len(await uow.applications.history("app_1")) == 2


async def test_unknown_application_returns_empty_history(uow_factory):
    async with uow_factory() as uow:
        assert await uow.applications.history("nope") == []


async def test_list_recent_returns_latest_state_of_each_application(uow_factory):
    """텔레그램 채팅 에이전트의 list_applications 도구가 쓴다 — 정렬 순서는 백엔드마다

    다를 수 있어(ports/repository.py 참고) 존재 여부/최신 상태만 본다.
    """
    async with uow_factory() as uow:
        await uow.applications.upsert_state(
            PersistState(
                application_id="app_1", workflow_run_id="run_1", state=ApplicationState.EVALUATING
            )
        )
        await uow.applications.upsert_state(
            PersistState(
                application_id="app_1", workflow_run_id="run_1", state=ApplicationState.COMPLETED
            )
        )
        await uow.applications.upsert_state(
            PersistState(
                application_id="app_2", workflow_run_id="run_1", state=ApplicationState.REJECTED
            )
        )
        await uow.commit()
    async with uow_factory() as uow:
        summaries = await uow.applications.list_recent(limit=10)

    by_id = {s.application_id: s for s in summaries}
    assert by_id.keys() == {"app_1", "app_2"}
    assert by_id["app_1"].state is ApplicationState.COMPLETED  # 최신 상태만, 이력 전체 아님
    assert by_id["app_2"].state is ApplicationState.REJECTED


async def test_list_recent_respects_limit(uow_factory):
    async with uow_factory() as uow:
        for i in range(3):
            await uow.applications.upsert_state(
                PersistState(
                    application_id=f"app_{i}",
                    workflow_run_id="run_1",
                    state=ApplicationState.EVALUATING,
                )
            )
        await uow.commit()
    async with uow_factory() as uow:
        assert len(await uow.applications.list_recent(limit=2)) == 2


async def test_latest_states_returns_only_the_latest_per_requested_id(uow_factory):
    """apply_intake.py 의 사전 필터가 쓰는 배치 조회 — 요청한 id 중 이력이 있는 것만, 그마저도

    최신 상태 하나씩만 돌려준다.
    """
    async with uow_factory() as uow:
        await uow.applications.upsert_state(
            PersistState(
                application_id="app_1", workflow_run_id="run_1", state=ApplicationState.EVALUATING
            )
        )
        await uow.applications.upsert_state(
            PersistState(
                application_id="app_1", workflow_run_id="run_1", state=ApplicationState.COMPLETED
            )
        )
        await uow.applications.upsert_state(
            PersistState(
                application_id="app_2", workflow_run_id="run_1", state=ApplicationState.REJECTED
            )
        )
        await uow.commit()
    async with uow_factory() as uow:
        states = await uow.applications.latest_states(["app_1", "app_2", "app_unknown"])

    assert states == {
        "app_1": ApplicationState.COMPLETED,
        "app_2": ApplicationState.REJECTED,
    }


async def test_latest_states_empty_ids_returns_empty_dict(uow_factory):
    async with uow_factory() as uow:
        assert await uow.applications.latest_states([]) == {}


async def test_satisfies_protocol(uow_factory):
    uow: UnitOfWork = uow_factory()
    assert hasattr(uow.applications, "upsert_state")
