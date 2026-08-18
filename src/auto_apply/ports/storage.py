from datetime import timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class BlobStore(Protocol):
    """파일 저장. 없는 키 조회 시 domain.errors.BlobNotFound 를 던진다 (계약)."""

    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> str: ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def presign(self, key: str, ttl: timedelta) -> str: ...
