import asyncio
import json
from pathlib import Path

from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.errors import PolicyViolation


class JsonFileRecipeSource:
    """Recipe 를 데이터로 관리한다는 원칙의 가장 단순한 구현 (§3).

    {root}/{platform}.json 에서 읽는다. Pydantic 검증을 통과하지 못하면 실행하지 않는다.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    async def active(self, platform: str) -> AutomationRecipe:
        path = self._root / f"{platform}.json"

        def _read() -> str:
            return path.read_text()

        try:
            raw = await asyncio.to_thread(_read)
        except FileNotFoundError as e:
            raise PolicyViolation(f"{platform}: active recipe 파일이 없다 ({path})") from e
        recipe = AutomationRecipe.model_validate(json.loads(raw))
        if recipe.status not in ("active", "candidate"):
            raise PolicyViolation(f"{platform}: recipe status={recipe.status} 로는 실행하지 않는다")
        return recipe
