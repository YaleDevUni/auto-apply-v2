from typing import Protocol

from auto_apply.contracts.fact import Fact


class FactSource(Protocol):
    """이력서 생성 파이프라인이 읽는 평평한 fact 목록 (§A7).

    원본은 저장소의 Experience 다 — 구현은 `domain/experience_facts.py` 로 변환해 돌려준다.
    캐시하지 않는다: 사용자가 경험을 고치면 바로 다음 생성부터 보여야 한다.
    """

    async def list_for_user(self, user_id: str) -> list[Fact]: ...
