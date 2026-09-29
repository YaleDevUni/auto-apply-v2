import asyncio
from pathlib import Path

import yaml

from auto_apply.contracts.fact import Fact


class YamlFactSource:
    """`config/facts.yaml`을 읽는다. 매번 새로 읽는다 — 캐시하지 않는다.

    캐시하면 사용자가 이력을 고쳐도 다음 지원 건까지 반영이 안 된다. 파일 하나 읽는 비용은
    무시할 만하다.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    async def list_for_user(self, user_id: str) -> list[Fact]:
        def _read() -> str:
            return self._path.read_text(encoding="utf-8")

        raw = await asyncio.to_thread(_read)
        data = yaml.safe_load(raw) or []
        facts = [Fact.model_validate(item) for item in data]
        return [f for f in facts if f.user_id == user_id]
