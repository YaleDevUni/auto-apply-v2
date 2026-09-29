from collections.abc import Callable

from auto_apply.contracts.profile import Profile
from auto_apply.domain.errors import ProfileNotFound
from auto_apply.ports.repository import UnitOfWork


class RepositoryProfileSource:
    """저장소의 인적사항을 이력서 파이프라인에 넘긴다 (§A7). 없으면 `ProfileNotFound`."""

    def __init__(self, uow: Callable[[], UnitOfWork]) -> None:
        self._uow = uow

    async def get(self, user_id: str) -> Profile:
        async with self._uow() as uow:
            profile = await uow.profiles.get(user_id)
        if profile is None:
            raise ProfileNotFound(f"user_id={user_id!r} 의 인적사항이 저장돼 있지 않다")
        return profile
