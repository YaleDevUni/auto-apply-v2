from typing import Protocol

from auto_apply.contracts.profile import Profile


class ProfileSource(Protocol):
    """`config/profile.yaml`(사람이 직접 채움)을 읽는 포트. `FactSource`와 같은 모양이다.

    없는 user_id 를 요청하면 `ProfileNotFound`(domain/errors.py) — 이력서 헤더를 채울 정형
    정보가 아예 없다는 뜻이라 재시도해도 결과가 같다.
    """

    async def get(self, user_id: str) -> Profile: ...
