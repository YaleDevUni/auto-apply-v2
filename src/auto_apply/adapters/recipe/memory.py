from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.errors import PolicyViolation


class InMemoryRecipeSource:
    def __init__(self, recipes: dict[str, AutomationRecipe] | None = None) -> None:
        self._recipes = dict(recipes or {})

    def put(self, recipe: AutomationRecipe) -> None:
        self._recipes[recipe.platform] = recipe

    async def active(self, platform: str) -> AutomationRecipe:
        recipe = self._recipes.get(platform)
        if recipe is None:
            raise PolicyViolation(f"{platform}: active recipe 가 없다")
        if recipe.status == "deprecated":
            raise PolicyViolation(f"{platform}: recipe v{recipe.version} 는 deprecated")
        return recipe
