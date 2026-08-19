"""ApplicationRepository contract test — 멱등성이 계약의 핵심이다 (§4.1).

activity 는 최소 1회 실행이므로 같은 값으로 두 번 불려도 결과가 같아야 한다.
"""

from datetime import UTC, datetime

import pytest

from auto_apply.adapters.repository.file import FileUnitOfWork
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.contracts.dto import ApplicationAttempt, PersistState
from auto_apply.contracts.job import ApplicabilityVerdict, JobPosting, JobRecord, ScreeningVerdict
from auto_apply.domain.enums import ApplicationState, AttemptOutcome, ExecutionMode
from auto_apply.ports.repository import UnitOfWork


@pytest.fixture(params=["memory", "file"])
def uow_factory(request: pytest.FixtureRequest, tmp_path):
    if request.param == "memory":
        rows: dict = {}
        job_rows: dict = {}
        attempt_rows: dict = {}
        return lambda: InMemoryUnitOfWork(rows, job_rows, attempt_rows)
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
    assert hasattr(uow.jobs, "upsert")
    assert hasattr(uow.attempts, "record")


# ─────────────────────────── AttemptRepository ───────────────────────────
# 실행 1회 = 1행, (application_id, attempt) 기준 멱등 upsert 가 계약의 핵심이다 (§4, §5).


def _attempt(attempt_no: int = 1, **kw) -> ApplicationAttempt:
    base: dict = {
        "application_id": "app_1",
        "attempt": attempt_no,
        "recipe_platform": "fixture",
        "recipe_version": 1,
        "mode": ExecutionMode.DRY_RUN,
        "outcome": AttemptOutcome.UNKNOWN,
        "started_at": datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
    }
    base.update(kw)
    return ApplicationAttempt(**base)


async def test_attempt_record_then_history(uow_factory):
    async with uow_factory() as uow:
        await uow.attempts.record(_attempt())
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.attempts.history("app_1")
    assert len(history) == 1
    assert history[0].outcome is AttemptOutcome.UNKNOWN


async def test_attempt_duplicate_record_does_not_duplicate_rows(uow_factory):
    """submit 직전 UNKNOWN 기록 → 결과로 덮어쓰기 (§5). 같은 attempt 번호는 1행이어야 한다."""
    async with uow_factory() as uow:
        await uow.attempts.record(_attempt())
        await uow.attempts.record(_attempt(outcome=AttemptOutcome.SUCCEEDED))
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.attempts.history("app_1")
    assert len(history) == 1
    assert history[0].outcome is AttemptOutcome.SUCCEEDED


async def test_different_attempts_are_appended_in_order(uow_factory):
    async with uow_factory() as uow:
        await uow.attempts.record(_attempt(1, outcome=AttemptOutcome.FAILED))
        await uow.attempts.record(_attempt(2, outcome=AttemptOutcome.SUCCEEDED))
        await uow.commit()
    async with uow_factory() as uow:
        history = await uow.attempts.history("app_1")
    assert [a.attempt for a in history] == [1, 2]
    assert [a.outcome for a in history] == [AttemptOutcome.FAILED, AttemptOutcome.SUCCEEDED]


async def test_unknown_application_returns_empty_attempt_history(uow_factory):
    async with uow_factory() as uow:
        assert await uow.attempts.history("nope") == []


# ─────────────────────────── JobRepository ───────────────────────────

_COLLECTED_AT = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _record(
    *, platform: str = "wanted", platform_job_id: str = "1", actionable: bool | None = True
) -> JobRecord:
    job = JobPosting(
        platform=platform,
        platform_job_id=platform_job_id,
        url="https://x/1",
        company="회사",
        title="백엔드 개발자",
    )
    screening = ScreeningVerdict(verdict="pass", track="dev", fit_score=80)
    applicability = (
        ApplicabilityVerdict(actionable=actionable, channel="platform_form", apply_url=job.url)
        if actionable is not None
        else None
    )
    return JobRecord(
        job=job, screening=screening, applicability=applicability, collected_at=_COLLECTED_AT
    )


async def test_job_upsert_then_get(uow_factory):
    async with uow_factory() as uow:
        await uow.jobs.upsert(_record())
        await uow.commit()
    async with uow_factory() as uow:
        record = await uow.jobs.get("wanted", "1")
    assert record is not None
    assert record.job.title == "백엔드 개발자"
    assert record.collected_at == _COLLECTED_AT


async def test_job_upsert_is_idempotent_by_platform_and_id(uow_factory):
    """같은 공고를 다시 수집해도(재실행) 최신 판정으로 덮어쓸 뿐 늘어나지 않는다."""
    async with uow_factory() as uow:
        await uow.jobs.upsert(_record())
        await uow.jobs.upsert(_record())
        await uow.commit()
    async with uow_factory() as uow:
        assert (await uow.jobs.get("wanted", "1")) is not None
        assert len(await uow.jobs.actionable()) == 1


async def test_job_upsert_overwrites_with_latest_verdict(uow_factory):
    async with uow_factory() as uow:
        await uow.jobs.upsert(_record(actionable=True))
        await uow.jobs.upsert(_record(actionable=False))
        await uow.commit()
    async with uow_factory() as uow:
        record = await uow.jobs.get("wanted", "1")
    assert record is not None
    assert record.applicability is not None
    assert record.applicability.actionable is False


async def test_unknown_job_returns_none(uow_factory):
    async with uow_factory() as uow:
        assert await uow.jobs.get("wanted", "nope") is None


async def test_actionable_excludes_non_actionable_and_screened_out(uow_factory):
    async with uow_factory() as uow:
        await uow.jobs.upsert(_record(platform_job_id="1", actionable=True))
        await uow.jobs.upsert(_record(platform_job_id="2", actionable=False))
        await uow.jobs.upsert(_record(platform_job_id="3", actionable=None))  # excluded, 판정 없음
        await uow.commit()
    async with uow_factory() as uow:
        actionable = await uow.jobs.actionable()
    assert {r.job.platform_job_id for r in actionable} == {"1"}


async def test_actionable_is_platform_scoped_correctly(uow_factory):
    """다른 플랫폼의 같은 platform_job_id는 다른 공고다."""
    async with uow_factory() as uow:
        await uow.jobs.upsert(_record(platform="wanted", platform_job_id="1"))
        await uow.jobs.upsert(_record(platform="saramin", platform_job_id="1"))
        await uow.commit()
    async with uow_factory() as uow:
        actionable = await uow.jobs.actionable()
    assert {(r.job.platform, r.job.platform_job_id) for r in actionable} == {
        ("wanted", "1"),
        ("saramin", "1"),
    }
