from typing import Protocol

from auto_apply.contracts.recipe import AutomationRecipe


class RecipeSource(Protocol):
    """Recipe 조회·버전관리 (§3, §9). 이력은 append-only — 버전을 덮어쓰지 않는다.

    검증되지 않은 자동화를 돌리는 것은 정책 위반이므로 조회/승격 실패는 모두
    domain.errors.PolicyViolation 으로 던진다 (반환값이 아니라 예외 계약).
    """

    async def active(self, platform: str) -> AutomationRecipe:
        """status 가 candidate|active 인 버전 중 version 이 가장 큰 것.

        candidate 가 active 보다 최신이면 candidate 를 우선한다 — §2.4 "candidate 의
        첫 실전 실행은 supervised mode" 가 성립하려면 그게 곧 "지금 돌아가는 recipe"여야
        한다. 둘 다 없으면(draft/deprecated 뿐이거나 아예 없으면) PolicyViolation.
        """
        ...

    async def versions(self, platform: str) -> list[AutomationRecipe]:
        """해당 platform 의 모든 버전, version 오름차순. 없으면 빈 리스트 (순수 조회라 안 던짐)."""
        ...

    async def save(self, recipe: AutomationRecipe) -> AutomationRecipe:
        """새 버전을 이력에 추가한다.

        - 이미 존재하는 (platform, version) 이면 PolicyViolation — 버전은 불변 이력이다.
        - status="active" 로는 저장할 수 없다 (PolicyViolation) — active 승격은 반드시
          promote() 를 거친다. "AI 가 만든 Recipe 를 active 로 바로 안 올린다" invariant 를
          지금까지는 사람이 손으로 지켰는데 이제 port 레벨에서도 막는다.
        """
        ...

    async def promote(self, platform: str, version: int) -> AutomationRecipe:
        """candidate 버전을 active 로 승격한다.

        대상이 status="candidate" 가 아니면 PolicyViolation(draft 를 바로 승격하거나 이미
        승격된 걸 또 승격하는 걸 막음). 같은 platform 의 기존 active 버전(들)은 deprecated 로
        내려간다 — platform 당 active 는 최대 1개라는 불변식을 유지한다.
        """
        ...
