"""ApplicationService 의 러너용 통로 — 실패 정리·크래시 복구 (§A3, §A9). memory·sqlite 둘 다."""

import pytest

from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.errors import FailureKind, NotFound
from auto_apply.services.application import ApplicationService
from tests.services.fakes import FixedClock, SeqIds

_PATHS = {
    S.QUEUED: [S.QUEUED],
    S.FILLING: [S.QUEUED, S.FILLING],
    S.NEEDS_LOGIN: [S.QUEUED, S.FILLING, S.NEEDS_LOGIN],
    S.AWAITING_APPROVAL: [S.QUEUED, S.FILLING, S.AWAITING_APPROVAL],
    S.REVISING: [S.QUEUED, S.FILLING, S.AWAITING_APPROVAL, S.REVISING],
    S.SUBMITTING: [S.QUEUED, S.FILLING, S.AWAITING_APPROVAL, S.SUBMITTING],
    S.CANCELLED: [S.CANCELLED],
}


@pytest.fixture
def service(uow_factory) -> ApplicationService:
    return ApplicationService(uow_factory, FixedClock(), SeqIds())


async def make_in(service: ApplicationService, state: S) -> str:
    record = await service.create("https://jobs.example.com/p/1")
    for s in _PATHS[state]:
        await service.transition(record.application_id, s, run_id="run_x")
    return record.application_id


async def test_recover_interrupted_restores_resumable_states(service):
    ids = {s: await make_in(service, s) for s in _PATHS}
    recovered = await service.recover_interrupted(reason="crash_recovery")
    assert recovered == {
        ids[S.FILLING]: S.QUEUED,
        ids[S.REVISING]: S.AWAITING_APPROVAL,
        ids[S.SUBMITTING]: S.INCIDENT,  # 클릭이 나갔는지 모른다 — FAILED 로 숨기지 않는다
    }
    for state, app in ids.items():
        expected = recovered.get(app, state)
        assert await service.current_state(app) is expected


async def test_settle_failure_maps_domain_failures(service):
    app = await make_in(service, S.FILLING)
    assert (
        await service.settle_failure(app, FailureKind.NEEDS_LOGIN, run_id="r", reason="login")
        is S.NEEDS_LOGIN
    )
    assert await service.current_state(app) is S.NEEDS_LOGIN


async def test_settle_failure_leaves_cancelled_alone(service):
    app = await make_in(service, S.FILLING)
    await service.transition(app, S.CANCELLED, run_id=None, reason="사람이 취소")
    assert await service.settle_failure(app, FailureKind.FATAL, run_id="r", reason="x") is None
    assert await service.current_state(app) is S.CANCELLED


async def test_settle_failure_unknown_application_is_noop(service):
    assert await service.settle_failure("nope", FailureKind.FATAL, run_id=None, reason="x") is None
    with pytest.raises(NotFound):
        await service.current_state("nope")


async def test_settle_failure_in_submitting_is_incident(service):
    app = await make_in(service, S.SUBMITTING)
    got = await service.settle_failure(app, FailureKind.TRANSIENT, run_id="r", reason="x")
    assert got is S.INCIDENT
