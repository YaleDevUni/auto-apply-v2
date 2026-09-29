"""ApplicationRepository contract test — 상태 이력은 append-only, 최신 = 마지막 전이 (§A3).

같은 run 안에서도 A→B→A 로 되돌아올 수 있다(FILLING↔NEEDS_INPUT). 중복 전이를 거르는 건
저장소가 아니라 전이 검증(M4 `ApplicationService.transition()`)의 몫이다.
`uow_factory`(tests/conftest.py)가 memory·sqlite 둘 다로 돈다.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from auto_apply.adapters.repository.sqlite import build_engine
from auto_apply.contracts.dto import PersistState
from auto_apply.domain.enums import ApplicationState
from auto_apply.ports.repository import UnitOfWork


def _state(state: ApplicationState, run_id: str = "run_1", **kw) -> PersistState:
    return PersistState(application_id="app_1", workflow_run_id=run_id, state=state, **kw)


async def test_upsert_then_history(uow_factory):
    async with uow_factory() as uow:
        await uow.applications.upsert_state(_state(ApplicationState.EVALUATING))
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.applications.history("app_1")
    assert [h.state for h in history] == [ApplicationState.EVALUATING]


async def test_every_call_appends_a_row(uow_factory):
    """저장소는 중복을 합치지 않는다 — 부른 만큼 이력이 쌓이고, 값은 각 행에 그대로 남는다."""
    at = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    async with uow_factory() as uow:
        await uow.applications.upsert_state(_state(ApplicationState.SCHEDULED))
        await uow.applications.upsert_state(_state(ApplicationState.SCHEDULED, scheduled_at=at))
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.applications.history("app_1")
    assert [h.scheduled_at for h in history] == [None, at]


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
    """정렬 순서는 백엔드마다 다를 수 있어(ports/repository.py 참고) 존재 여부/최신 상태만 본다."""
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
    """배치 조회 — 요청한 id 중 이력이 있는 것만, 최신 상태 하나씩만 돌려준다."""
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


async def test_uncommitted_writes_are_rolled_back(uow_factory, request):
    if request.node.callspec.params["uow_factory"] == "memory":
        pytest.skip("memory 대역은 트랜잭션이 없다")
    async with uow_factory() as uow:
        await uow.applications.upsert_state(_state(ApplicationState.EVALUATING))
    async with uow_factory() as uow:
        assert await uow.applications.history("app_1") == []


async def test_return_to_earlier_state_in_same_run_is_latest(uow_factory):
    """회귀: 같은 run 에서 A→B→A 면 최신은 A, 이력 3행 (예전 (run, state) 멱등키는 B 를 남겼다)."""
    async with uow_factory() as uow:
        for s in (
            ApplicationState.EXECUTING,
            ApplicationState.NEEDS_HUMAN,
            ApplicationState.EXECUTING,
        ):
            await uow.applications.upsert_state(_state(s))
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.applications.history("app_1")
        latest = await uow.applications.latest_states(["app_1"])
        [summary] = await uow.applications.list_recent()
    assert [h.state for h in history] == [
        ApplicationState.EXECUTING,
        ApplicationState.NEEDS_HUMAN,
        ApplicationState.EXECUTING,
    ]
    assert latest == {"app_1": ApplicationState.EXECUTING}
    assert summary.state is ApplicationState.EXECUTING


async def test_sqlite_enforces_foreign_keys(sqlite_url):
    """runs 가 없는 지원 건을 가리키지 못한다 — 연결마다 PRAGMA foreign_keys 가 켜져 있어야 한다."""
    engine = build_engine(sqlite_url)
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO runs (id, application_id, kind, status, started_at)"
                    " VALUES ('r1', 'missing', 'fill', 'running', '2026-09-30 00:00:00')"
                )
            )
    await engine.dispose()
