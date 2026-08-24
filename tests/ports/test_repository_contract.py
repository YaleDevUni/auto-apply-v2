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
from auto_apply.contracts.dto import (
    ApplicationAttempt,
    CachedResume,
    PersistState,
    RenderedPdf,
    ResumeDraft,
    ScheduleConfig,
)
from auto_apply.contracts.job import ApplicabilityVerdict, JobPosting, JobRecord, ScreeningVerdict
from auto_apply.domain.enums import ApplicationState, AttemptOutcome, ExecutionMode
from auto_apply.ports.repository import UnitOfWork

_PG_TABLES = "application_state_history, jobs, application_attempts, schedule_configs, resume_cache"


@pytest.fixture(params=["memory", "file", pytest.param("postgres", marks=pytest.mark.docker)])
async def uow_factory(request: pytest.FixtureRequest, tmp_path):
    if request.param == "memory":
        rows: dict = {}
        job_rows: dict = {}
        attempt_rows: dict = {}
        schedule_config_rows: dict = {}
        resume_rows: dict = {}
        yield lambda: InMemoryUnitOfWork(
            rows, job_rows, attempt_rows, schedule_config_rows, resume_rows
        )
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


async def test_schedule_config_unknown_target_returns_none(uow_factory):
    async with uow_factory() as uow:
        assert await uow.schedule_config.get("apply") is None


async def test_schedule_config_set_then_get_roundtrips(uow_factory):
    config = ScheduleConfig(target="apply", hour=14, minute=30, count=5)
    async with uow_factory() as uow:
        await uow.schedule_config.set(config)
        await uow.commit()
    async with uow_factory() as uow:
        got = await uow.schedule_config.get("apply")
    assert got == config


async def test_schedule_config_set_overwrites_previous_value_for_same_target(uow_factory):
    """멱등 upsert — target당 최신값 1건만 남는다(이력 아님)."""
    async with uow_factory() as uow:
        await uow.schedule_config.set(ScheduleConfig(target="collection", hour=9, minute=0))
        await uow.commit()
    async with uow_factory() as uow:
        await uow.schedule_config.set(
            ScheduleConfig(target="collection", hour=11, minute=30, platforms=["wanted"])
        )
        await uow.commit()
    async with uow_factory() as uow:
        got = await uow.schedule_config.get("collection")
    assert got == ScheduleConfig(target="collection", hour=11, minute=30, platforms=["wanted"])


async def test_schedule_config_targets_are_independent(uow_factory):
    async with uow_factory() as uow:
        await uow.schedule_config.set(ScheduleConfig(target="collection", hour=9, minute=0))
        await uow.schedule_config.set(ScheduleConfig(target="apply", hour=10, minute=0, count=3))
        await uow.commit()
    async with uow_factory() as uow:
        collection = await uow.schedule_config.get("collection")
        apply = await uow.schedule_config.get("apply")
    assert collection is not None and collection.hour == 9
    assert apply is not None and apply.hour == 10 and apply.count == 3


# ─────────────────────────── ResumeRepository ───────────────────────────
# application_id 당 최신값 1건만 (§2.3) — REJECTED/EXPIRED 재지원 때 LLM을 다시 안 부르려는
# 캐시. ScheduleConfigRepository 와 같은 upsert 모양이다.


def _cached_resume(application_id: str = "app_1", *, blob_key: str = "resumes/u1/r1.pdf"):
    return CachedResume(
        application_id=application_id,
        draft=ResumeDraft(resume_id="r1", content={"summary": "..."}, used_fact_ids=["f1"]),
        pdf=RenderedPdf(blob_key=blob_key, bytes_written=1234),
    )


async def test_resume_cache_unknown_application_returns_none(uow_factory):
    async with uow_factory() as uow:
        assert await uow.resumes.get("nope") is None


async def test_resume_cache_save_then_get_roundtrips(uow_factory):
    resume = _cached_resume()
    async with uow_factory() as uow:
        await uow.resumes.save(resume)
        await uow.commit()
    async with uow_factory() as uow:
        got = await uow.resumes.get("app_1")
    assert got == resume


async def test_resume_cache_save_overwrites_previous_value_for_same_application(uow_factory):
    """멱등 upsert — application_id 당 최신값 1건만 남는다(이력 아님) — REVISE 로 갱신된 결과가

    이전 캐시를 덮어쓰는 경로가 이걸 요구한다(§2.3 CachedResume).
    """
    async with uow_factory() as uow:
        await uow.resumes.save(_cached_resume(blob_key="resumes/u1/r1.pdf"))
        await uow.commit()
    async with uow_factory() as uow:
        await uow.resumes.save(_cached_resume(blob_key="resumes/u1/r2.pdf"))
        await uow.commit()
    async with uow_factory() as uow:
        got = await uow.resumes.get("app_1")
    assert got is not None
    assert got.pdf.blob_key == "resumes/u1/r2.pdf"


async def test_resume_cache_applications_are_independent(uow_factory):
    async with uow_factory() as uow:
        await uow.resumes.save(_cached_resume("app_1", blob_key="resumes/u1/a.pdf"))
        await uow.resumes.save(_cached_resume("app_2", blob_key="resumes/u1/b.pdf"))
        await uow.commit()
    async with uow_factory() as uow:
        app_1 = await uow.resumes.get("app_1")
        app_2 = await uow.resumes.get("app_2")
    assert app_1 is not None and app_1.pdf.blob_key == "resumes/u1/a.pdf"
    assert app_2 is not None and app_2.pdf.blob_key == "resumes/u1/b.pdf"
