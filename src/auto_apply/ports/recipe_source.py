from typing import Protocol

from auto_apply.contracts.recipe import AutomationRecipe


class RecipeSource(Protocol):
    """active recipe 조회. 없으면 domain.errors.PolicyViolation (계약).

    active 가 없는데 실행을 시도하는 것은 정책 위반이다 — 검증되지 않은 자동화를 돌리는 것이므로.
    """

    async def active(self, platform: str) -> AutomationRecipe: ...
