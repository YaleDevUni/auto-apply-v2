from datetime import timedelta

from auto_apply.domain.errors import BlobNotFound


class InMemoryBlobStore:
    """테스트 대역. BlobStore contract test 의 두 번째 구현 (§11.1 원칙 3)."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> str:
        self._blobs[key] = data
        return key

    async def get(self, key: str) -> bytes:
        try:
            return self._blobs[key]
        except KeyError as e:
            raise BlobNotFound(key) from e

    async def exists(self, key: str) -> bool:
        return key in self._blobs

    async def presign(self, key: str, ttl: timedelta) -> str:
        if key not in self._blobs:
            raise BlobNotFound(key)
        return f"memory://{key}?ttl={int(ttl.total_seconds())}"
