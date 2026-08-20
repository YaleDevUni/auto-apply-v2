"""RecipeSource contract test — 검증되지 않은 Recipe 로는 실행하지 않는다 (§3).

memory/jsonfile 두 구현이 같은 스위트를 통과해야 한다 (§11.1 원칙 3).
"""

import json

import pytest

from auto_apply.adapters.recipe.jsonfile import JsonFileRecipeSource
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.errors import PolicyViolation
from tests.conftest import sample_recipe


@pytest.fixture(params=["memory", "jsonfile"])
def make_source(request: pytest.FixtureRequest, tmp_path):
    def _make(status: str = "active"):
        recipe = sample_recipe(status=status)
        if request.param == "memory":
            return InMemoryRecipeSource({"fixture": recipe})
        platform_dir = tmp_path / "fixture"
        platform_dir.mkdir()
        (platform_dir / "1.json").write_text(json.dumps(recipe.model_dump(mode="json")))
        return JsonFileRecipeSource(tmp_path)

    return _make


def _v2(status: str) -> AutomationRecipe:
    return sample_recipe(status=status).model_copy(update={"version": 2})


async def test_active_recipe_is_returned(make_source):
    recipe = await make_source("active").active("fixture")
    assert recipe.platform == "fixture"
    assert recipe.status == "active"


async def test_candidate_is_allowed_for_supervised_run(make_source):
    """candidate 는 조회는 되지만, 실행 모드는 supervised 로 강제된다 (§2.4)."""
    assert (await make_source("candidate").active("fixture")).status == "candidate"


async def test_deprecated_recipe_is_refused(make_source):
    with pytest.raises(PolicyViolation):
        await make_source("deprecated").active("fixture")


async def test_missing_platform_raises_policy_violation(make_source):
    with pytest.raises(PolicyViolation):
        await make_source().active("unknown-platform")


async def test_versions_for_unknown_platform_is_empty(make_source):
    assert await make_source().versions("unknown-platform") == []


async def test_save_new_version_appears_in_versions(make_source):
    source = make_source("active")
    await source.save(_v2("candidate"))
    versions = await source.versions("fixture")
    assert [r.version for r in versions] == [1, 2]


async def test_active_prefers_newer_candidate_over_active(make_source):
    """§2.4 — candidate 의 첫 실전 실행은 supervised mode. 지금 실행 대상이어야 성립한다."""
    source = make_source("active")
    await source.save(_v2("candidate"))
    active = await source.active("fixture")
    assert (active.version, active.status) == (2, "candidate")


async def test_save_active_status_is_rejected(make_source):
    source = make_source("active")
    with pytest.raises(PolicyViolation):
        await source.save(_v2("active"))


async def test_save_duplicate_version_is_rejected(make_source):
    source = make_source("active")
    with pytest.raises(PolicyViolation):
        await source.save(sample_recipe(status="candidate"))  # version=1, 이미 존재


async def test_promote_non_candidate_is_rejected(make_source):
    source = make_source("active")
    await source.save(_v2("draft"))
    with pytest.raises(PolicyViolation):
        await source.promote("fixture", 2)


async def test_promote_missing_version_is_rejected(make_source):
    with pytest.raises(PolicyViolation):
        await make_source("active").promote("fixture", 99)


async def test_promote_candidate_deprecates_old_active(make_source):
    source = make_source("active")  # v1 active
    await source.save(_v2("candidate"))

    promoted = await source.promote("fixture", 2)
    assert (promoted.version, promoted.status) == (2, "active")

    statuses = {r.version: r.status for r in await source.versions("fixture")}
    assert statuses == {1: "deprecated", 2: "active"}
    assert (await source.active("fixture")).version == 2
