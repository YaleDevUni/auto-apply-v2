from auto_apply.contracts.fact import Fact


class StaticFactSource:
    """네트워크·파일 없이 파이프라인을 돌리기 위한 어댑터. 테스트 대역."""

    def __init__(self, facts: list[Fact] | None = None) -> None:
        self._facts = list(facts or [])

    async def list_for_user(self, user_id: str) -> list[Fact]:
        return [f for f in self._facts if f.user_id == user_id]
