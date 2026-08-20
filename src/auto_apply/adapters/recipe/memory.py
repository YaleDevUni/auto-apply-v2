from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.errors import PolicyViolation

_LIVE_STATUSES = ("active", "candidate")


class InMemoryRecipeSource:
    """RecipeSource contract test 의 두 번째 구현 (§11.1 원칙 3)."""

    def __init__(self, recipes: dict[str, AutomationRecipe] | None = None) -> None:
        # platform -> version -> recipe. 생성자는 "이 recipe 가 그 platform 의 유일 버전"으로
        # 시딩하는 옛 시그니처를 유지한다 — tests/conftest.py 등 기존 호출부를 안 건드리려고.
        self._by_platform: dict[str, dict[int, AutomationRecipe]] = {
            platform: {recipe.version: recipe} for platform, recipe in (recipes or {}).items()
        }

    def put(self, recipe: AutomationRecipe) -> None:
        """테스트 전용 — save() 의 append-only/active 검증을 우회해서 임의 상태를 강제로 심는다."""
        self._by_platform.setdefault(recipe.platform, {})[recipe.version] = recipe

    async def versions(self, platform: str) -> list[AutomationRecipe]:
        by_version = self._by_platform.get(platform, {})
        return [by_version[v] for v in sorted(by_version)]

    async def active(self, platform: str) -> AutomationRecipe:
        live = [r for r in await self.versions(platform) if r.status in _LIVE_STATUSES]
        if not live:
            raise PolicyViolation(f"{platform}: active recipe 가 없다")
        return max(live, key=lambda r: r.version)

    async def save(self, recipe: AutomationRecipe) -> AutomationRecipe:
        if recipe.status == "active":
            raise PolicyViolation(f"{recipe.platform} v{recipe.version}: active 는 promote 로만")
        by_version = self._by_platform.setdefault(recipe.platform, {})
        if recipe.version in by_version:
            raise PolicyViolation(f"{recipe.platform} v{recipe.version}: 이미 존재하는 버전")
        by_version[recipe.version] = recipe
        return recipe

    async def promote(self, platform: str, version: int) -> AutomationRecipe:
        by_version = self._by_platform.get(platform, {})
        target = by_version.get(version)
        if target is None or target.status != "candidate":
            raise PolicyViolation(f"{platform} v{version}: candidate 상태가 아니면 승격할 수 없다")

        promoted = target.model_copy(update={"status": "active"})
        by_version[version] = promoted
        for v, r in list(by_version.items()):
            if v != version and r.status == "active":
                by_version[v] = r.model_copy(update={"status": "deprecated"})
        return promoted
