import asyncio
import json
from pathlib import Path

from auto_apply.adapters.recipe._status import no_live_reason
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.errors import PolicyViolation

_LIVE_STATUSES = ("active", "candidate")
# promote() 가 함께 내리는 상태들 — quarantined 가 여기 있어야 수선 성공이 곧 격리 해제가
# 된다(§2.4a). 안 그러면 새 버전이 active 가 돼도 옛 버전이 격리 상태로 영원히 남는다.
_DEMOTABLE_STATUSES = ("active", "quarantined")


class JsonFileRecipeSource:
    """Recipe 를 데이터로 관리한다는 원칙의 가장 단순한 구현 (§3).

    {root}/{platform}/{version}.json 에 버전마다 파일 하나. 락은 없다 — 이 프로젝트의
    파일 어댑터 전반과 같은 "단일 프로세스 전제"를 그대로 이어받는다. 옛 레이아웃
    ({root}/{platform}.json, 버전 이력 없이 파일 하나)에서 옮겨오려면
    scripts/migrate_recipes_to_versioned.py 를 먼저 돌려야 한다.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _dir(self, platform: str) -> Path:
        return self._root / platform

    def _path(self, platform: str, version: int) -> Path:
        return self._dir(platform) / f"{version}.json"

    async def versions(self, platform: str) -> list[AutomationRecipe]:
        def _read_all() -> list[AutomationRecipe]:
            # glob 은 디렉토리가 없어도 그냥 빈 결과를 준다(예외 없음) — "버전 이력 없음"이
            # 정상 상태라 여기서 굳이 존재 여부를 따로 안 본다.
            paths = self._dir(platform).glob("*.json")
            recipes = [AutomationRecipe.model_validate(json.loads(p.read_text())) for p in paths]
            return sorted(recipes, key=lambda r: r.version)

        return await asyncio.to_thread(_read_all)

    async def active(self, platform: str) -> AutomationRecipe:
        versions = await self.versions(platform)
        live = [r for r in versions if r.status in _LIVE_STATUSES]
        if not live:
            raise PolicyViolation(no_live_reason(platform, versions))
        return max(live, key=lambda r: r.version)

    async def quarantine(self, platform: str) -> AutomationRecipe:
        target = await self.active(platform)
        quarantined = target.model_copy(update={"status": "quarantined"})
        await asyncio.to_thread(self._write, quarantined)
        return quarantined

    async def unquarantine(self, platform: str) -> AutomationRecipe:
        held = [r for r in await self.versions(platform) if r.status == "quarantined"]
        if not held:
            raise PolicyViolation(f"{platform}: 격리된 recipe 가 없다")
        restored = max(held, key=lambda r: r.version).model_copy(update={"status": "candidate"})
        await asyncio.to_thread(self._write, restored)
        return restored

    async def save(self, recipe: AutomationRecipe) -> AutomationRecipe:
        if recipe.status == "active":
            raise PolicyViolation(f"{recipe.platform} v{recipe.version}: active 는 promote 로만")
        path = self._path(recipe.platform, recipe.version)

        def _write_new() -> None:
            if path.exists():
                raise PolicyViolation(f"{recipe.platform} v{recipe.version}: 이미 존재하는 버전")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(recipe.model_dump(mode="json")))

        await asyncio.to_thread(_write_new)
        return recipe

    async def promote(self, platform: str, version: int) -> AutomationRecipe:
        versions = await self.versions(platform)
        by_version = {r.version: r for r in versions}
        target = by_version.get(version)
        if target is None or target.status != "candidate":
            raise PolicyViolation(f"{platform} v{version}: candidate 상태가 아니면 승격할 수 없다")

        promoted = target.model_copy(update={"status": "active"})
        demoted = [
            r.model_copy(update={"status": "deprecated"})
            for r in versions
            if r.status in _DEMOTABLE_STATUSES
        ]

        def _write_all() -> None:
            for recipe in (promoted, *demoted):
                self._write(recipe)

        await asyncio.to_thread(_write_all)
        return promoted

    def _write(self, recipe: AutomationRecipe) -> None:
        self._path(recipe.platform, recipe.version).write_text(
            json.dumps(recipe.model_dump(mode="json"))
        )
