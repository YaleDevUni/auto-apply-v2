import asyncio
from datetime import timedelta
from pathlib import Path

from auto_apply.domain.errors import BlobNotFound


class LocalBlobStore:
    """로컬 파일시스템. MinIO 없이 개발할 때 쓴다 (§11.2)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        # 경로 탈출 방지: 정규화 후 root 밖이면 거부
        candidate = (self._root / key).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"key 가 root 를 벗어난다: {key}")
        return candidate

    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> str:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)
        return key

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as e:
            raise BlobNotFound(key) from e

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def presign(self, key: str, ttl: timedelta) -> str:
        if not await self.exists(key):
            raise BlobNotFound(key)
        return self._path(key).as_uri()
