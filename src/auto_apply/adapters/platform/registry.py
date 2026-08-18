from collections.abc import Sequence

from auto_apply.domain.errors import PolicyViolation
from auto_apply.ports.platform import PlatformAdapter


class StaticPlatformRegistry:
    """allowlist 방식. 등록되지 않은 도메인에는 자동화를 돌리지 않는다 (§3)."""

    def __init__(self, adapters: Sequence[PlatformAdapter]) -> None:
        self._adapters = list(adapters)

    def for_url(self, url: str) -> PlatformAdapter:
        for adapter in self._adapters:
            if adapter.matches(url):
                return adapter
        raise PolicyViolation(f"등록되지 않은 플랫폼 URL: {url}")

    def for_platform(self, platform: str) -> PlatformAdapter:
        for adapter in self._adapters:
            if adapter.platform == platform:
                return adapter
        raise PolicyViolation(f"등록되지 않은 플랫폼: {platform}")
