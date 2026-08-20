import asyncio
from pathlib import Path

import yaml

from auto_apply.contracts.portfolio import PortfolioMap


class YamlPortfolioSource:
    """`config/portfolio_map.yaml`을 읽는다. `YamlProfileSource`와 같은 이유로 매번 새로 읽는다."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def get(self, user_id: str) -> PortfolioMap:
        def _read() -> str:
            return self._path.read_text(encoding="utf-8")

        try:
            raw = await asyncio.to_thread(_read)
        except FileNotFoundError:
            return PortfolioMap(user_id=user_id, categories={})
        data = yaml.safe_load(raw) or []
        for item in data:
            m = PortfolioMap.model_validate(item)
            if m.user_id == user_id:
                return m
        return PortfolioMap(user_id=user_id, categories={})
