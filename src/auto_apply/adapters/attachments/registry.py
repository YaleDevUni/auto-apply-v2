from collections.abc import Sequence

from auto_apply.domain.errors import PolicyViolation
from auto_apply.ports.attachments import AttachmentManager


class StaticAttachmentRegistry:
    """`adapters/platform/registry.StaticPlatformRegistry` 와 같은 allowlist 패턴."""

    def __init__(self, managers: Sequence[AttachmentManager]) -> None:
        self._managers = list(managers)

    def for_platform(self, platform: str) -> AttachmentManager:
        for manager in self._managers:
            if manager.platform == platform:
                return manager
        raise PolicyViolation(f"첨부파일 관리가 등록되지 않은 플랫폼: {platform}")
