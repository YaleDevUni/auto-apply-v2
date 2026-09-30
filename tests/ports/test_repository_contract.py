"""ApplicationRepository contract test — 상태 이력은 append-only, 최신 = 마지막 전이 (§A3).

같은 run 안에서도 A→B→A 로 되돌아올 수 있다(FILLING↔NEEDS_INPUT). 전이 표 검증은 저장소가 아니라
`ApplicationService.transition()` 몫이고, 저장소는 `expected` 로 동시 전이만 거른다.
`uow_factory`(tests/conftest.py)가 memory·sqlite 둘 다로 돈다.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from auto_apply.adapters.repository.sqlite import build_engine
from auto_apply.contracts.dto import ApplicationRecord, PersistState
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.enums import SubmitMode
from auto_apply.domain.errors import InvalidInput, InvalidTransition, NotFound
from auto_apply.ports.repository import UnitOfWork

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _state(state: S, app: str = "app_1", run_id: str | None = "run_1", **kw) -> PersistState:
    kw.setdefault("at", T0)
    return PersistState(application_id=app, run_id=run_id, state=state, **kw)


def _record(app: str = "app_1", **kw) -> ApplicationRecord:
    kw.setdefault("url", "https://jobs.example.com/p/1")
    kw.setdefault("domain", "jobs.example.com")
    return ApplicationRecord(application_id=app, **kw)


async def _seed(uow_factory, app: str, *states: S) -> None:
    """DRAFT 로 만들고 `states` 순서대로 쌓는다(표 검증 없이 — 저장소 계약만 본다)."""
    async with uow_factory() as uow:
        await uow.applications.add(_record(app), _state(S.DRAFT, app, run_id=None))
        prev = S.DRAFT
        for s in states:
            await uow.applications.append_state(_state(s, app), expected=prev)
            prev = s
        await uow.commit()


async def test_add_then_get_and_history(uow_factory):
    async with uow_factory() as uow:
        await uow.applications.add(
            _record(submit_mode=SubmitMode.LIVE), _state(S.DRAFT, run_id=None)
        )
        await uow.commit()
    async with uow_factory() as uow:
        record = await uow.applications.get("app_1")
        history = await uow.applications.history("app_1")
    assert record == _record(submit_mode=SubmitMode.LIVE)
    assert history == [_state(S.DRAFT, run_id=None)]


async def test_record_defaults_to_dry_run(uow_factory):
    await _seed(uow_factory, "app_1")
    async with uow_factory() as uow:
        record = await uow.applications.get("app_1")
    assert record is not None and record.submit_mode is SubmitMode.DRY_RUN


async def test_add_twice_is_rejected(uow_factory):
    await _seed(uow_factory, "app_1")
    async with uow_factory() as uow:
        with pytest.raises(InvalidInput):
            await uow.applications.add(_record(), _state(S.DRAFT, run_id=None))


async def test_get_unknown_is_none(uow_factory):
    async with uow_factory() as uow:
        assert await uow.applications.get("nope") is None


async def test_every_append_is_a_new_row_with_its_own_values(uow_factory):
    """저장소는 합치지 않는다 — 부른 만큼 이력이 쌓이고 각 행의 값은 그대로 남는다."""
    await _seed(uow_factory, "app_1", S.QUEUED)
    async with uow_factory() as uow:
        later = _state(S.FILLING, at=T0 + timedelta(minutes=1), reason="started")
        await uow.applications.append_state(later, expected=S.QUEUED)
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.applications.history("app_1")
    assert [h.state for h in history] == [S.DRAFT, S.QUEUED, S.FILLING]
    assert history[-1] == later


async def test_append_with_stale_expected_writes_nothing(uow_factory):
    """다른 쪽이 먼저 전이했으면(expected 불일치) 쓰지 않는다 — 이력·스냅샷 둘 다 그대로."""
    await _seed(uow_factory, "app_1", S.QUEUED)
    async with uow_factory() as uow:
        with pytest.raises(InvalidTransition):
            await uow.applications.append_state(_state(S.FILLING), expected=S.DRAFT)
        await uow.commit()
    async with uow_factory() as uow:
        assert [h.state for h in await uow.applications.history("app_1")] == [S.DRAFT, S.QUEUED]
        assert await uow.applications.latest_states(["app_1"]) == {"app_1": S.QUEUED}


async def test_append_to_unknown_application_is_not_found(uow_factory):
    async with uow_factory() as uow:
        with pytest.raises(NotFound):
            await uow.applications.append_state(_state(S.QUEUED), expected=S.DRAFT)


async def test_retry_of_same_state_on_new_run_is_separate(uow_factory):
    """재실행(run_id 변경)은 별도 이력이다. 어느 run 이 무엇을 했는지 추적 가능해야 한다."""
    await _seed(uow_factory, "app_1", S.QUEUED)
    async with uow_factory() as uow:
        await uow.applications.append_state(_state(S.FILLING, run_id="run_1"), expected=S.QUEUED)
        await uow.applications.append_state(_state(S.QUEUED, run_id="run_1"), expected=S.FILLING)
        await uow.applications.append_state(_state(S.FILLING, run_id="run_2"), expected=S.QUEUED)
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.applications.history("app_1")
    assert [h.run_id for h in history] == [None, "run_1", "run_1", "run_1", "run_2"]


async def test_unknown_application_returns_empty_history(uow_factory):
    async with uow_factory() as uow:
        assert await uow.applications.history("nope") == []


async def test_list_recent_returns_latest_state_of_each_application(uow_factory):
    """정렬 순서는 백엔드마다 다를 수 있어(ports/repository.py 참고) 존재 여부/최신 상태만 본다."""
    await _seed(uow_factory, "app_1", S.QUEUED, S.FILLING)
    await _seed(uow_factory, "app_2")
    async with uow_factory() as uow:
        summaries = await uow.applications.list_recent(limit=10)
    by_id = {s.application_id: s for s in summaries}
    assert by_id.keys() == {"app_1", "app_2"}
    assert by_id["app_1"].state is S.FILLING  # 최신 상태만, 이력 전체 아님
    assert by_id["app_1"].at == T0
    assert by_id["app_2"].state is S.DRAFT


async def test_list_recent_respects_limit(uow_factory):
    for i in range(3):
        await _seed(uow_factory, f"app_{i}")
    async with uow_factory() as uow:
        assert len(await uow.applications.list_recent(limit=2)) == 2


async def test_latest_states_returns_only_the_latest_per_requested_id(uow_factory):
    """배치 조회 — 요청한 id 중 있는 것만, 최신 상태 하나씩만 돌려준다."""
    await _seed(uow_factory, "app_1", S.QUEUED, S.FILLING)
    await _seed(uow_factory, "app_2", S.QUEUED)
    async with uow_factory() as uow:
        states = await uow.applications.latest_states(["app_1", "app_2", "app_unknown"])
    assert states == {"app_1": S.FILLING, "app_2": S.QUEUED}


async def test_latest_states_empty_ids_returns_empty_dict(uow_factory):
    async with uow_factory() as uow:
        assert await uow.applications.latest_states([]) == {}


async def test_satisfies_protocol(uow_factory):
    uow: UnitOfWork = uow_factory()
    assert hasattr(uow.applications, "append_state")
    assert not hasattr(uow.applications, "upsert_state")  # v2 이름은 사라졌다


async def test_uncommitted_writes_are_rolled_back(uow_factory, request):
    if request.node.callspec.params["uow_factory"] == "memory":
        pytest.skip("memory 대역은 트랜잭션이 없다")
    async with uow_factory() as uow:
        await uow.applications.add(_record(), _state(S.DRAFT, run_id=None))
    async with uow_factory() as uow:
        assert await uow.applications.history("app_1") == []
        assert await uow.applications.get("app_1") is None


async def test_return_to_earlier_state_in_same_run_is_latest(uow_factory):
    """회귀: 같은 run 에서 A→B→A 면 최신은 A, 이력 3행 (예전 (run, state) 멱등키는 B 를 남겼다)."""
    await _seed(uow_factory, "app_1", S.QUEUED, S.FILLING, S.NEEDS_INPUT, S.FILLING)
    async with uow_factory() as uow:
        history = await uow.applications.history("app_1")
        latest = await uow.applications.latest_states(["app_1"])
        [summary] = await uow.applications.list_recent()
    assert [h.state for h in history][-3:] == [S.FILLING, S.NEEDS_INPUT, S.FILLING]
    assert latest == {"app_1": S.FILLING}
    assert summary.state is S.FILLING


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


async def test_in_states_returns_only_matching_latest(uow_factory):
    await _seed(uow_factory, "app_1", S.QUEUED, S.FILLING)
    await _seed(uow_factory, "app_2", S.QUEUED)
    await _seed(uow_factory, "app_3", S.QUEUED, S.FILLING, S.AWAITING_APPROVAL, S.REVISING)
    async with uow_factory() as uow:
        found = await uow.applications.in_states([S.FILLING, S.REVISING, S.SUBMITTING])
        assert await uow.applications.in_states([]) == {}
    assert found == {"app_1": S.FILLING, "app_3": S.REVISING}
