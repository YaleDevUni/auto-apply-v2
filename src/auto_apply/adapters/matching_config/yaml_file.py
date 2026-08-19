import asyncio
from pathlib import Path

import yaml

from auto_apply.contracts.matching_config import MatchingConfig


class YamlMatchingConfigSource:
    """`config/matching.yaml`을 읽는다. 매번 새로 읽는다 — 캐시하지 않는다.

    캐시하면 사용자가 규칙을 고쳐도 다음 수집 주기까지 반영이 안 된다. 파일 하나
    읽는 비용은 무시할 만하다 (RecipeSource.jsonfile 과 같은 판단).
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    async def load(self) -> MatchingConfig:
        def _read() -> str:
            return self._path.read_text(encoding="utf-8")

        raw = await asyncio.to_thread(_read)
        data = yaml.safe_load(raw) or {}
        return MatchingConfig.model_validate(data)
