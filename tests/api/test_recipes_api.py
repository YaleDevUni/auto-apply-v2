"""§7 GET/POST /recipes/{platform} — RecipeSource 를 얇게 노출할 뿐이라 Temporal 이 필요 없다.

applications 라우터 테스트(tests/api/test_applications_api.py)와 달리 workflow signal/query 가
없어서 `_Workers`/`WorkflowEnvironment` 없이 컨테이너만 갈아끼운 ASGI 클라이언트로 충분하다.
"""

import httpx
import pytest
from httpx import ASGITransport

from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.api.main import app
from tests.conftest import Harness, sample_recipe


@pytest.fixture
async def client():
    h = Harness()
    container = h.container()
    app.state.container = container
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, container


async def test_list_versions_returns_seeded_active_recipe(client):
    ac, _container = client
    resp = await ac.get("/recipes/fixture")
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform"] == "fixture"
    assert [v["version"] for v in body["versions"]] == [1]
    assert body["versions"][0]["status"] == "active"


async def test_list_versions_unknown_platform_returns_empty(client):
    ac, _container = client
    resp = await ac.get("/recipes/no-such-platform")
    assert resp.status_code == 200
    assert resp.json()["versions"] == []


async def test_promote_candidate_to_active(client):
    ac, container = client
    recipes = container.recipes
    assert isinstance(recipes, InMemoryRecipeSource)
    recipes.put(sample_recipe(status="candidate").model_copy(update={"version": 2}))

    resp = await ac.post("/recipes/fixture/promote", json={"version": 2})
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    got = (await ac.get("/recipes/fixture")).json()["versions"]
    versions = {v["version"]: v["status"] for v in got}
    assert versions == {1: "deprecated", 2: "active"}


async def test_promote_draft_is_rejected(client):
    ac, container = client
    recipes = container.recipes
    assert isinstance(recipes, InMemoryRecipeSource)
    recipes.put(sample_recipe(status="draft").model_copy(update={"version": 3}))

    resp = await ac.post("/recipes/fixture/promote", json={"version": 3})
    assert resp.status_code == 409


async def test_promote_missing_version_is_rejected(client):
    ac, _container = client
    resp = await ac.post("/recipes/fixture/promote", json={"version": 99})
    assert resp.status_code == 409
