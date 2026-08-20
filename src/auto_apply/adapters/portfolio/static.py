from auto_apply.contracts.portfolio import PortfolioMap


class StaticPortfolioSource:
    """네트워크·파일 없이 파이프라인을 돌리기 위한 어댑터. 테스트 대역."""

    def __init__(self, maps: list[PortfolioMap] | None = None) -> None:
        self._maps = list(maps or [])

    async def get(self, user_id: str) -> PortfolioMap:
        for m in self._maps:
            if m.user_id == user_id:
                return m
        return PortfolioMap(user_id=user_id, categories={})
