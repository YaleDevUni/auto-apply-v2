"""RecipeSource contract test — 검증되지 않은 Recipe 로는 실행하지 않는다 (§3)."""

import json

import pytest

from auto_apply.adapters.recipe.jsonfile import JsonFileRecipeSource
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.domain.errors import PolicyViolation
from tests.conftest import sample_recipe


@pytest.fixture(params=["memory", "jsonfile"])
def make_source(request: pytest.FixtureRequest, tmp_path):
    def _make(status: str = "active"):
        recipe = sample_recipe(status=status)
        if request.param == "memory":
            return InMemoryRecipeSource({"fixture": recipe})
        (tmp_path / "fixture.json").write_text(json.dumps(recipe.model_dump(mode="json")))
        return JsonFileRecipeSource(tmp_path)

    return _make


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
