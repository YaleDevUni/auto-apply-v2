"""§7 Recipe 버전관리 엔드포인트.

지금까지 승격은 손으로 recipe JSON 의 status 를 고치거나 M4 AutomationRepairWorkflow 의
Telegram 승인 흐름으로만 가능했다 — 그 흐름 밖에서(예: 사람이 새 recipe 를 직접 만들어
candidate 로 저장해둔 경우) 승격할 방법이 없었다. RecipeSource.promote() port 는 이미
"AI 가 만든 Recipe 는 candidate 상태여야만 승격 가능" invariant 를 강제하므로, 여기서는
그 port 를 얇게 노출하기만 한다.
"""

from fastapi import APIRouter, HTTPException

from auto_apply.api.deps import ContainerDep
from auto_apply.api.schemas import PromoteRecipeRequest, RecipeVersionsResponse
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.errors import PolicyViolation

router = APIRouter(prefix="/recipes", tags=["recipes"])


@router.get("/{platform}")
async def list_recipe_versions(platform: str, c: ContainerDep) -> RecipeVersionsResponse:
    versions = await c.recipes.versions(platform)
    return RecipeVersionsResponse(platform=platform, versions=versions)


@router.post("/{platform}/promote")
async def promote_recipe(
    platform: str, req: PromoteRecipeRequest, c: ContainerDep
) -> AutomationRecipe:
    """candidate → active. draft 를 바로 승격하거나 이미 승격된 걸 또 승격하면 409."""
    try:
        return await c.recipes.promote(platform, req.version)
    except PolicyViolation as e:
        raise HTTPException(409, str(e)) from e
