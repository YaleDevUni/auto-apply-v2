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

    async def delete(self, key: str) -> bool:
        """지웠으면 True, 원래 없었으면 False (에러 아님 — 재시도해도 안전하게)."""
        ...

    async def list_keys(self, prefix: str) -> list[str]:
        """`prefix`(`/` 로 끝나는 디렉터리 꼴, 아니면 ValueError) 아래 모든 키를 정렬해서.

        하위 디렉터리 키도 포함한다. 아무것도 없으면 빈 목록.
        """
        ...
