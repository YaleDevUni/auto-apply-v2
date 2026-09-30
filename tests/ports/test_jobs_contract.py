"""JobRepository·RunRepository contract test (§A9, §A3). memory·sqlite 둘 다로 돈다."""

from datetime import UTC, datetime, timedelta

import pytest

from auto_apply.contracts.dto import ApplicationRecord, PersistState
from auto_apply.domain.enums import ApplicationState, RunKind, RunStatus
from auto_apply.domain.errors import InvalidInput, NotFound, UniqueIdentifierRejected
from auto_apply.ports.jobs import BROWSER_KINDS, JobKind, JobRecord, JobStatus, RunRecord

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
ALL = frozenset(JobKind)
RRN = "900101-1234568"


def _job(job_id: str, kind: JobKind = JobKind.FILL, *, at: datetime = T0, **kw) -> JobRecord:
    kw.setdefault("application_id", "app_1")
    return JobRecord(job_id=job_id, kind=kind, created_at=at, run_after=kw.pop("after", at), **kw)


@pytest.fixture
async def uow(uow_factory):
    """지원 건 app_1 이 있는 저장소 (jobs·runs 는 applications 를 참조한다)."""
    async with uow_factory() as u:
        await u.applications.add(
            ApplicationRecord(application_id="app_1", url="https://x.com/1", domain="x.com"),
            PersistState(application_id="app_1", run_id=None, state=ApplicationState.DRAFT, at=T0),
        )
        await u.commit()
    return uow_factory


async def _enqueue(uow_factory, *jobs: JobRecord) -> None:
    async with uow_factory() as u:
        for job in jobs:
            await u.jobs.enqueue(job)
        await u.commit()


async def _claim(uow_factory, kinds=ALL, now=T0) -> JobRecord | None:
    async with uow_factory() as u:
        job = await u.jobs.claim_next(kinds, now=now)
        await u.commit()
    return job


async def test_enqueue_then_claim_oldest_first_and_only_once(uow):
    await _enqueue(
        uow,
        _job("j2", at=T0 + timedelta(seconds=1)),
        _job("j1"),
        _job("j0", application_id=None, kind=JobKind.REFLECT),
    )
    first = await _claim(uow, BROWSER_KINDS)
    assert first is not None and first.job_id == "j1"
    assert first.status is JobStatus.RUNNING and first.started_at == T0
    assert await _claim(uow, BROWSER_KINDS) is None  # j2 는 아직 만들어지기 전 시각
    second = await _claim(uow, BROWSER_KINDS, now=T0 + timedelta(seconds=1))
    assert second is not None and second.job_id == "j2"
    assert await _claim(uow, BROWSER_KINDS, now=T0 + timedelta(seconds=1)) is None
    other = await _claim(uow, ALL - BROWSER_KINDS)
    assert other is not None and other.job_id == "j0" and other.application_id is None


async def test_claim_respects_run_after(uow):
    await _enqueue(uow, _job("j1", after=T0 + timedelta(seconds=30)))
    assert await _claim(uow, now=T0) is None
    claimed = await _claim(uow, now=T0 + timedelta(seconds=30))
    assert claimed is not None and claimed.job_id == "j1"


async def test_duplicate_enqueue_rejected(uow):
    await _enqueue(uow, _job("j1"))
    with pytest.raises(InvalidInput):
        await _enqueue(uow, _job("j1"))


async def test_finish_and_requeue_only_running(uow):
    await _enqueue(uow, _job("j1"), _job("j2"))
    await _claim(uow)
    later = T0 + timedelta(seconds=5)
    async with uow() as u:
        await u.jobs.finish("j1", JobStatus.DONE, at=later)
        with pytest.raises(InvalidInput):  # 이미 끝난 job
            await u.jobs.finish("j1", JobStatus.FAILED, at=later)
        with pytest.raises(InvalidInput):  # 아직 대기 중
            await u.jobs.requeue("j2", attempt=2, run_after=later, error="x")
        with pytest.raises(NotFound):
            await u.jobs.finish("nope", JobStatus.DONE, at=later)
        with pytest.raises(InvalidInput):  # 종료 상태가 아니다
            await u.jobs.finish("j2", JobStatus.RUNNING, at=later)
        await u.commit()
    async with uow() as u:
        done = await u.jobs.get("j1")
    assert done is not None and done.status is JobStatus.DONE and done.finished_at == later

    await _claim(uow)
    async with uow() as u:
        await u.jobs.requeue("j2", attempt=2, run_after=later, error="BrowserLaunchFailed: x")
        await u.commit()
    async with uow() as u:
        again = await u.jobs.get("j2")
    assert again is not None
    assert (again.status, again.attempt, again.run_after, again.started_at) == (
        JobStatus.QUEUED,
        2,
        later,
        None,
    )
    assert again.error == "BrowserLaunchFailed: x"


async def test_interrupt_running_leaves_queued(uow):
    await _enqueue(uow, _job("j1"), _job("j2"))
    await _claim(uow)
    async with uow() as u:
        interrupted = await u.jobs.interrupt_running(at=T0)
        await u.commit()
    assert [j.job_id for j in interrupted] == ["j1"]
    async with uow() as u:
        assert [j.job_id for j in await u.jobs.list_by_status(JobStatus.INTERRUPTED)] == ["j1"]
        assert [j.job_id for j in await u.jobs.list_by_status(JobStatus.QUEUED)] == ["j2"]


async def test_unique_identifier_never_stored(uow):
    """절대 규칙 5: payload·에러 문자열에 주민번호가 있으면 저장하지 않는다."""
    with pytest.raises(
        ValueError, match="고유식별정보"
    ):  # pydantic 이 감싼 UniqueIdentifierRejected
        _job("j1", payload={"memo": RRN})
    await _enqueue(uow, _job("j1"))
    await _claim(uow)
    async with uow() as u:
        with pytest.raises(UniqueIdentifierRejected):
            await u.jobs.finish("j1", JobStatus.FAILED, at=T0, error=f"x {RRN}")
        with pytest.raises(UniqueIdentifierRejected):
            await u.jobs.requeue("j1", attempt=2, run_after=T0, error=RRN)


def _run(run_id: str = "run_1") -> RunRecord:
    return RunRecord(run_id=run_id, application_id="app_1", kind=RunKind.FILL, started_at=T0)


async def test_run_start_finish(uow):
    later = T0 + timedelta(minutes=3)
    async with uow() as u:
        await u.runs.start(_run())
        with pytest.raises(InvalidInput):
            await u.runs.start(_run())
        await u.commit()
    async with uow() as u:
        await u.runs.finish(
            "run_1",
            RunStatus.DONE,
            at=later,
            result="awaiting_approval",
            input_tokens=10,
            output_tokens=3,
            transcript_path="runs/run_1/transcript.jsonl",
        )
        with pytest.raises(InvalidInput):  # 이미 닫힘
            await u.runs.finish("run_1", RunStatus.FAILED, at=later)
        with pytest.raises(NotFound):
            await u.runs.finish("nope", RunStatus.FAILED, at=later)
        with pytest.raises(InvalidInput):
            await u.runs.finish("run_1", RunStatus.RUNNING, at=later)
        await u.commit()
    async with uow() as u:
        run = await u.runs.get("run_1")
    assert run == RunRecord(
        run_id="run_1",
        application_id="app_1",
        kind=RunKind.FILL,
        status=RunStatus.DONE,
        started_at=T0,
        finished_at=later,
        result="awaiting_approval",
        input_tokens=10,
        output_tokens=3,
        transcript_path="runs/run_1/transcript.jsonl",
    )


async def test_run_interrupt_running(uow):
    async with uow() as u:
        await u.runs.start(_run("run_1"))
        await u.runs.start(_run("run_2"))
        await u.runs.finish("run_2", RunStatus.FAILED, at=T0, error="x")
        assert await u.runs.interrupt_running(at=T0) == ["run_1"]
        await u.commit()
    async with uow() as u:
        run = await u.runs.get("run_1")
        with pytest.raises(UniqueIdentifierRejected):
            await u.runs.finish("run_1", RunStatus.FAILED, at=T0, error=RRN)
    assert run is not None and run.status is RunStatus.INTERRUPTED and run.finished_at == T0
