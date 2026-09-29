from typing import Protocol

from auto_apply.contracts.fact import Fact


class FactSource(Protocol):
    """`config/facts.yaml`(사람이 직접 채움)을 읽는 포트.

    UnitOfWork 밖에 독립된 읽기 전용 포트. DB 저장으로의 교체는 M1 프로필·지식베이스 몫(§A7).
    """

    async def list_for_user(self, user_id: str) -> list[Fact]: ...
