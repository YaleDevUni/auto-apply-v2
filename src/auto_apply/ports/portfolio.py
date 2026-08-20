from typing import Protocol

from auto_apply.contracts.portfolio import PortfolioMap


class PortfolioSource(Protocol):
    """`config/portfolio_map.yaml`(사람이 직접 채움)을 읽는 포트. `ProfileSource`와 같은 모양이다.

    `ProfileSource`와 다르게 없는 user_id 를 요청해도 예외를 던지지 않고 빈 매핑
    (`PortfolioMap(user_id=..., categories={})`)을 돌려준다 — Profile 은 이력서 헤더를 채우는
    필수 정보라 없으면 생성을 막아야 하지만, 포트폴리오 카테고리는 "매칭되는 게 없으면 그냥
    포트폴리오를 안 붙인다"로 안전하게 낮춰 잡을 수 있는 부가 정보라서다.
    """

    async def get(self, user_id: str) -> PortfolioMap: ...
