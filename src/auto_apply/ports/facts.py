from typing import Protocol

from auto_apply.contracts.fact import Fact


class FactSource(Protocol):
    """`config/facts.yaml`(사람이 직접 채움)을 읽는 포트. `MatchingConfigSource`와 같은 모양이다 —

    UnitOfWork 밖에 독립된 읽기 전용 포트. Postgres 이관은 지금 하지 않는다(§9.1 repository들과
    같은 나중 교체 지점만 확보).
    """

    async def list_for_user(self, user_id: str) -> list[Fact]: ...
