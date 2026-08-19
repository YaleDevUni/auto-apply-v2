"""JobCollectionWorkflow → collect_platform_jobs 이름 매칭 + 플랫폼별 fan-out 검증.

@pytest.mark.integration — Temporal test server 바이너리가 필요하다 (§ test_ping.py).
"""

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from auto_apply.activities.job_collection import JobCollectionActivities
from auto_apply.adapters.clock.system import SystemClock
from auto_apply.adapters.job_source.fixture import FixtureJobSource
from auto_apply.adapters.matching_config.static import StaticMatchingConfigSource
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.contracts.job import CollectJobsInput
from auto_apply.contracts.matching_config import ApplicabilityRules, MatchingConfig, TrackRule
from auto_apply.domain.errors import PolicyViolation
from auto_apply.temporal_config import DATA_CONVERTER
from auto_apply.workflows.job_collection import JobCollectionWorkflow
from tests.conftest import sample_recipe

pytestmark = pytest.mark.integration

CFG = MatchingConfig(
    tracks={"dev": TrackRule(label="개발", weight=100, keywords=["백엔드"])},
    applicability=ApplicabilityRules(min_fit_score=10, min_description_chars=0),
)


def _activities(tmp_path, platforms: list[str]) -> JobCollectionActivities:
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    for p in platforms:
        (auth_dir / f"{p}.json").write_text("{}")
    recipes = InMemoryRecipeSource({p: sample_recipe(status="active") for p in platforms})
    sources = [FixtureJobSource(platform=p) for p in platforms]
    return JobCollectionActivities(
        job_sources=sources,
        matching_config=StaticMatchingConfigSource(CFG),
        recipes=recipes,
        clock=SystemClock(),
        uow=lambda: InMemoryUnitOfWork({}, {}),
        auth_dir=auth_dir,
    )


async def test_collects_multiple_platforms_concurrently(tmp_path):
    acts = _activities(tmp_path, ["wanted", "saramin"])

    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        client: Client = env.client
        async with Worker(
            client,
            task_queue="test",
            workflows=[JobCollectionWorkflow],
            activities=acts.all(),
        ):
            result = await client.execute_workflow(
                JobCollectionWorkflow.run,
                CollectJobsInput(platforms=["wanted", "saramin"]),
                id="job-collection-1",
                task_queue="test",
            )

    by_platform = {r.platform: r for r in result.results}
    assert set(by_platform) == {"wanted", "saramin"}
    for r in by_platform.values():
        assert r.found == 1
        assert r.actionable == 1
        assert r.error is None


async def test_one_platform_failure_does_not_lose_the_others_result(tmp_path):
    """등록 안 된 플랫폼(PolicyViolation, non-retryable)이 섞여도 나머지는 정상 보고된다."""
    acts = _activities(tmp_path, ["wanted"])

    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        client: Client = env.client
        async with Worker(
            client,
            task_queue="test",
            workflows=[JobCollectionWorkflow],
            activities=acts.all(),
        ):
            result = await client.execute_workflow(
                JobCollectionWorkflow.run,
                CollectJobsInput(platforms=["wanted", "unknown-platform"]),
                id="job-collection-2",
                task_queue="test",
            )

    by_platform = {r.platform: r for r in result.results}
    assert by_platform["wanted"].error is None
    assert by_platform["wanted"].actionable == 1
    assert PolicyViolation.__name__ in (by_platform["unknown-platform"].error or "")
