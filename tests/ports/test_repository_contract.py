"""ApplicationRepository contract test — 멱등성이 계약의 핵심이다 (§4.1).

activity 는 최소 1회 실행이므로 같은 값으로 두 번 불려도 결과가 같아야 한다.
"""

from datetime import UTC, datetime

import pytest

from auto_apply.adapters.repository.file import FileUnitOfWork
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.contracts.dto import PersistState
from auto_apply.domain.enums import ApplicationState
from auto_apply.ports.repository import UnitOfWork


@pytest.fixture(params=["memory", "file"])
def uow_factory(request: pytest.FixtureRequest, tmp_path):
    if request.param == "memory":
        rows: dict = {}
        return lambda: InMemoryUnitOfWork(rows)
    return lambda: FileUnitOfWork(tmp_path)


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


async def test_satisfies_protocol(uow_factory):
    uow: UnitOfWork = uow_factory()
    assert hasattr(uow.applications, "upsert_state")
