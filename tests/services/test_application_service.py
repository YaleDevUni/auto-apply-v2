"""ApplicationService — 상태 쓰기 유일 통로 (§A3, 절대 규칙 6). memory·sqlite 둘 다로 돈다."""

import asyncio

import pytest
import structlog

from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.enums import SubmitMode
from auto_apply.domain.errors import InvalidInput, InvalidTransition, NotFound
from auto_apply.services.application import ApplicationService
from tests.services.fakes import FixedClock, SeqIds


@pytest.fixture
def service(uow_factory) -> ApplicationService:
    return ApplicationService(uow_factory, FixedClock(), SeqIds())


async def _states(uow_factory, app_id: str) -> list[S]:
    async with uow_factory() as uow:
        return [h.state for h in await uow.applications.history(app_id)]


async def test_create_is_draft_dry_run_with_domain(service, uow_factory):
    record = await service.create("https://Jobs.Example.com/p/1?x=1")
    assert record.submit_mode is SubmitMode.DRY_RUN
    assert record.domain == "jobs.example.com"
    async with uow_factory() as uow:
        assert await uow.applications.get(record.application_id) == record
        [initial] = await uow.applications.history(record.application_id)
    assert initial.state is S.DRAFT and initial.run_id is None


@pytest.mark.parametrize("url", ["", "ftp://x.com/a", "javascript:alert(1)", "https://", "jobs"])
async def test_create_rejects_non_http_url(service, url):
    with pytest.raises(InvalidInput):
        await service.create(url)


async def test_full_happy_path_appends_history(service, uow_factory):
    app = (await service.create("https://jobs.example.com/1")).application_id
    await service.transition(app, S.QUEUED, run_id=None, reason="trigger")
    await service.transition(app, S.FILLING, run_id="run_1")
    await service.transition(app, S.NEEDS_INPUT, run_id="run_1", reason="question")
    await service.transition(app, S.FILLING, run_id="run_1")
    await service.transition(app, S.AWAITING_APPROVAL, run_id="run_1")
    await service.transition(app, S.SUBMITTING, run_id=None, reason="approved")
    last = await service.transition(app, S.SUBMITTED, run_id="run_2")

    assert last.submitted_at == FixedClock().now()
    assert await _states(uow_factory, app) == [
        S.DRAFT, S.QUEUED, S.FILLING, S.NEEDS_INPUT, S.FILLING,
        S.AWAITING_APPROVAL, S.SUBMITTING, S.SUBMITTED,
    ]  # fmt: skip
    async with uow_factory() as uow:
        history = await uow.applications.history(app)
    assert [h.run_id for h in history][:3] == [None, None, "run_1"]
    assert history[1].reason == "trigger"


async def test_forbidden_transition_writes_nothing(service, uow_factory):
    app = (await service.create("https://jobs.example.com/1")).application_id
    with pytest.raises(InvalidTransition):
        await service.transition(app, S.SUBMITTING, run_id="run_1")  # 승인 없이 제출 단계로
    with pytest.raises(InvalidTransition):
        await service.transition(app, S.AWAITING_APPROVAL, run_id="run_1")
    assert await _states(uow_factory, app) == [S.DRAFT]


async def test_final_state_is_final(service, uow_factory):
    app = (await service.create("https://jobs.example.com/1")).application_id
    await service.transition(app, S.CANCELLED, run_id=None)
    with pytest.raises(InvalidTransition):
        await service.transition(app, S.QUEUED, run_id=None)
    assert await _states(uow_factory, app) == [S.DRAFT, S.CANCELLED]


async def test_incident_is_closed_by_human_only(service, uow_factory):
    app = (await service.create("https://jobs.example.com/1")).application_id
    for to in (S.QUEUED, S.FILLING, S.INCIDENT):
        await service.transition(app, to, run_id="run_1")
    for retry in (S.QUEUED, S.FILLING, S.FAILED):
        with pytest.raises(InvalidTransition):
            await service.transition(app, retry, run_id="run_2")
    await service.transition(app, S.SUBMITTED, run_id=None, reason="사람이 사이트에서 확인")
    assert (await _states(uow_factory, app))[-2:] == [S.INCIDENT, S.SUBMITTED]


async def test_unknown_application_is_not_found(service):
    with pytest.raises(NotFound):
        await service.transition("nope", S.QUEUED, run_id=None)


async def test_concurrent_transitions_from_same_state_only_one_wins(service, uow_factory):
    """러너 두 개가 같은 QUEUED 를 집어 동시에 FILLING 으로 — 하나만 이력에 남는다."""
    app = (await service.create("https://jobs.example.com/1")).application_id
    await service.transition(app, S.QUEUED, run_id=None)

    results = await asyncio.gather(
        service.transition(app, S.FILLING, run_id="run_1"),
        service.transition(app, S.FILLING, run_id="run_2"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, InvalidTransition) for r in results) == 1
    assert await _states(uow_factory, app) == [S.DRAFT, S.QUEUED, S.FILLING]


async def test_stale_read_is_rejected_by_repository(uow_factory):
    """서비스가 표를 확인한 뒤 쓰기 전에 다른 쪽이 전이하면(QUEUED→CANCELLED) 표 검증을 통과한
    QUEUED→FILLING 이라도 쓰지 않는다 — 취소된 건이 되살아나지 않게.

    sqlite 는 `BEGIN IMMEDIATE` 라 트랜잭션 안에 다른 쓰기가 끼지 못한다(끼우면 잠금 대기로 멈춘다).
    그래서 끼어든 전이를 먼저 쓰고, 서비스가 읽는 값만 그 전 상태(QUEUED)로 낡게 돌려준다."""
    plain = ApplicationService(uow_factory, FixedClock(), SeqIds())
    app = (await plain.create("https://jobs.example.com/1")).application_id
    await plain.transition(app, S.QUEUED, run_id=None)
    await plain.transition(app, S.CANCELLED, run_id=None)  # 서비스가 읽은 뒤 끼어든 전이

    class _Interleaving:
        def __init__(self) -> None:
            self._uow = uow_factory()

        async def __aenter__(self):
            await self._uow.__aenter__()

            async def stale(ids):
                return {app: S.QUEUED}

            self._uow.applications.latest_states = stale
            return self._uow

        async def __aexit__(self, *exc):
            return await self._uow.__aexit__(*exc)

    racing = ApplicationService(_Interleaving, FixedClock(), SeqIds())
    with pytest.raises(InvalidTransition):
        await racing.transition(app, S.FILLING, run_id="run_1")
    assert await _states(uow_factory, app) == [S.DRAFT, S.QUEUED, S.CANCELLED]


async def test_transition_logs_structured_ids(service):
    app = (await service.create("https://jobs.example.com/1")).application_id
    with structlog.testing.capture_logs() as logs:
        await service.transition(app, S.QUEUED, run_id=None)
    [entry] = [e for e in logs if e["event"] == "application.transition"]
    assert entry["application_id"] == app and "run_id" in entry
    assert (entry["from_state"], entry["to_state"]) == ("draft", "queued")
