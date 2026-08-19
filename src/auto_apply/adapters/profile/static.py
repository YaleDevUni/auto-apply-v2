from auto_apply.contracts.profile import Profile
from auto_apply.domain.errors import ProfileNotFound


class StaticProfileSource:
    """네트워크·파일 없이 파이프라인을 돌리기 위한 어댑터. 테스트 대역."""

    def __init__(self, profiles: list[Profile] | None = None) -> None:
        self._profiles = list(profiles or [])

    async def get(self, user_id: str) -> Profile:
        for profile in self._profiles:
            if profile.user_id == user_id:
                return profile
        raise ProfileNotFound(f"user_id={user_id!r} 의 profile 이 없다 (StaticProfileSource)")
