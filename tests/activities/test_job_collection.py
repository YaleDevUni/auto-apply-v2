"""JobCollectionActivities — 순수 domain(screen/evaluate_applicability)을 실제

포트 조합(JobSource/MatchingConfigSource/RecipeSource/UnitOfWork) 위에서 검증한다.
`ActivityEnvironment`는 순수 in-process 대역이라 Temporal 서버가 필요 없다
(WorkflowEnvironment 와 다르다 — 그래서 integration 마커를 붙이지 않는다).
"""

from collections.abc import AsyncIterator

import pytest
from temporalio.testing import ActivityEnvironment

from auto_apply.activities.job_collection import JobCollectionActivities
from auto_apply.adapters.clock.system import SystemClock
from auto_apply.adapters.job_source.fixture import FixtureJobSource
from auto_apply.adapters.matching_config.static import StaticMatchingConfigSource
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.contracts.job import JobPosting
from auto_apply.contracts.matching_config import ApplicabilityRules, MatchingConfig, TrackRule
from auto_apply.domain.errors import PolicyViolation
from tests.conftest import sample_recipe

CFG = MatchingConfig(
    tracks={"dev": TrackRule(label="개발", weight=100, keywords=["백엔드"])},
    applicability=ApplicabilityRules(min_fit_score=10, min_description_chars=100),
)


def _job(job_id: str, *, title: str, description: str = "") -> JobPosting:
    return JobPosting(
        platform="fixture",
        platform_job_id=job_id,
        url=f"https://fixture.local/jobs/{job_id}",
        company="Fixture Inc.",
        title=title,
        description=description,
    )


JOBS = [
    _job("1", title="백엔드 개발자", description="충분한 설명입니다. " * 20),  # 통과 + actionable
    _job("2", title="아무 상관없는 잡담"),  # OFF_TRACK
    _job("3", title="백엔드 개발자 후속"),  # 통과하지만 enrich 후에도 본문이 짧아 NO_DETAIL
]


def _activities(
    *, recipe_exists: bool = True, session_ok: bool = True, tmp_path
) -> tuple[JobCollectionActivities, dict, dict]:
    rows: dict = {}
    job_rows: dict = {}
    recipes = InMemoryRecipeSource(
        {"fixture": sample_recipe(status="active")} if recipe_exists else {}
    )
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    if session_ok:
        (auth_dir / "fixture.json").write_text("{}")

    acts = JobCollectionActivities(
        job_sources=[FixtureJobSource(JOBS)],
        matching_config=StaticMatchingConfigSource(CFG),
        recipes=recipes,
        clock=SystemClock(),
        uow=lambda: InMemoryUnitOfWork(rows, job_rows),
        auth_dir=auth_dir,
        detail_limit=10,
    )
    return acts, rows, job_rows


async def test_collect_platform_persists_screened_and_actionable_jobs(tmp_path):
    acts, _, job_rows = _activities(tmp_path=tmp_path)
    env = ActivityEnvironment()

    result = await env.run(acts.collect_platform_jobs, "fixture")

    assert result.found == 3
    assert result.passed == 2  # "1", "3" (dev 트랙 매치) — "2"는 OFF_TRACK
    assert result.excluded == 1
    assert result.actionable == 1  # "1"만 본문이 충분하고 나머지 조건도 통과
    assert result.enrich_errors == 0

    assert len(job_rows) == 3
    rec1 = job_rows[("fixture", "1")]
    assert rec1.applicability is not None
    assert rec1.applicability.actionable is True

    rec2 = job_rows[("fixture", "2")]
    assert rec2.screening.verdict == "excluded"
    assert rec2.applicability is None  # 스크리닝에서 걸린 공고는 지원가능성 판정 자체를 안 한다

    rec3 = job_rows[("fixture", "3")]
    assert rec3.screening.verdict == "pass"
    assert rec3.applicability is not None
    assert rec3.applicability.actionable is False
    codes = {b.code for b in rec3.applicability.blockers}
    assert "NO_DETAIL" in codes


async def test_missing_recipe_blocks_actionable_but_still_screens(tmp_path):
    acts, _, job_rows = _activities(tmp_path=tmp_path, recipe_exists=False)
    env = ActivityEnvironment()

    result = await env.run(acts.collect_platform_jobs, "fixture")

    assert result.passed == 2
    assert result.actionable == 0
    rec1 = job_rows[("fixture", "1")]
    assert rec1.applicability is not None
    assert rec1.applicability.actionable is False
    assert "NO_RECIPE" in {b.code for b in rec1.applicability.blockers}


async def test_missing_session_blocks_actionable(tmp_path):
    acts, _, job_rows = _activities(tmp_path=tmp_path, session_ok=False)
    env = ActivityEnvironment()

    await env.run(acts.collect_platform_jobs, "fixture")

    rec1 = job_rows[("fixture", "1")]
    assert rec1.applicability is not None
    assert "LOGIN_REQUIRED" in {b.code for b in rec1.applicability.blockers}


async def test_unknown_platform_raises_policy_violation(tmp_path):
    acts, _, _ = _activities(tmp_path=tmp_path)
    env = ActivityEnvironment()

    with pytest.raises(PolicyViolation):
        await env.run(acts.collect_platform_jobs, "unknown-platform")


class _FlakyEnrichSource:
    """job "2" 상세 조회만 실패하는 대역 — 한 건 실패가 전체를 막지 않는지 본다."""

    platform = "fixture"

    def __init__(self, jobs: list[JobPosting]) -> None:
        self._jobs = jobs

    async def list_jobs(self) -> AsyncIterator[JobPosting]:
        for job in self._jobs:
            yield job

    async def enrich(self, job: JobPosting) -> JobPosting:
        if job.platform_job_id == "2":
            raise RuntimeError("상세 조회 실패")
        return job.model_copy(update={"description": job.description or "충분한 설명 " * 30})


async def test_enrich_failure_on_one_job_does_not_abort_others(tmp_path):
    rows: dict = {}
    job_rows: dict = {}
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "fixture.json").write_text("{}")

    two_dev_jobs = [
        _job("1", title="백엔드 개발자"),
        _job("2", title="백엔드 개발자 둘째"),
    ]
    acts = JobCollectionActivities(
        job_sources=[_FlakyEnrichSource(two_dev_jobs)],
        matching_config=StaticMatchingConfigSource(CFG),
        recipes=InMemoryRecipeSource({"fixture": sample_recipe(status="active")}),
        clock=SystemClock(),
        uow=lambda: InMemoryUnitOfWork(rows, job_rows),
        auth_dir=auth_dir,
    )
    env = ActivityEnvironment()

    result = await env.run(acts.collect_platform_jobs, "fixture")

    assert result.enrich_errors == 1
    assert job_rows[("fixture", "1")].applicability.actionable is True  # 정상 enrich
    assert job_rows[("fixture", "2")].applicability.actionable is False  # 본문 없어 NO_DETAIL
