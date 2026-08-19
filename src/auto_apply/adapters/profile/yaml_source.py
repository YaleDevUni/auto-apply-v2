import asyncio
from pathlib import Path

import yaml

from auto_apply.contracts.profile import Profile
from auto_apply.domain.errors import ProfileNotFound


class YamlProfileSource:
    """`config/profile.yaml`을 읽는다. `YamlFactSource`와 같은 이유로 매번 새로 읽는다 — 캐시하면
    사용자가 정보를 고쳐도 다음 이력서 생성까지 반영이 안 된다."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def get(self, user_id: str) -> Profile:
        def _read() -> str:
            return self._path.read_text(encoding="utf-8")

        raw = await asyncio.to_thread(_read)
        data = yaml.safe_load(raw) or []
        for item in data:
            profile = Profile.model_validate(item)
            if profile.user_id == user_id:
                return profile
        raise ProfileNotFound(f"user_id={user_id!r} 의 profile 이 없다: {self._path}")
