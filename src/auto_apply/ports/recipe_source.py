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

    async def quarantine(self, platform: str) -> AutomationRecipe:
        """지금 살아 있는(active|candidate) 버전을 status="quarantined" 로 내린다 (§2.4a).

        사람이 텔레그램에서 "이 recipe 는 진짜 깨졌다"를 확정했을 때만 불린다 —
        `AutomationRepairWorkflow` 가 수선을 시작하기 직전이다. 그 뒤로는 `active()` 가
        PolicyViolation 을 던져서 그 플랫폼의 지원 실행이 아예 시작되지 않는다("깨진 recipe
        로 계속 제출을 시도하지 않는다"). 살아 있는 버전이 없으면 PolicyViolation.
        """
        ...

    async def unquarantine(self, platform: str) -> AutomationRecipe:
        """격리를 푼다 — status 를 "candidate" 로 되돌린다(active 가 아니다).

        active 로 바로 되돌리지 않는 건 의도다: 한 번 사람이 "깨졌다"고 판정한 recipe 는
        신뢰를 잃었으므로, 돌아올 때는 `resolve_mode` 가 SUPERVISED 로 돌리는 candidate
        여야 한다(§2.4a) — 다음 실행에서 사람이 submit 직전을 눈으로 확인하고, 그게
        성공하면 그때 promote() 로 다시 active 가 된다. 격리된 버전이 없으면 PolicyViolation.
        """
        ...

    async def promote(self, platform: str, version: int) -> AutomationRecipe:
        """candidate 버전을 active 로 승격한다.

        대상이 status="candidate" 가 아니면 PolicyViolation(draft 를 바로 승격하거나 이미
        승격된 걸 또 승격하는 걸 막음). 같은 platform 의 기존 active/quarantined 버전(들)은
        deprecated 로 내려간다 — platform 당 active 는 최대 1개라는 불변식을 유지하고,
        수선이 성공하면 격리도 같이 풀린다(§2.4a).
        """
        ...
