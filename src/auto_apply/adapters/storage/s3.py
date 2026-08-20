import asyncio
from datetime import timedelta

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from auto_apply.domain.errors import BlobNotFound


def _is_not_found(err: ClientError) -> bool:
    code = err.response.get("Error", {}).get("Code", "")
    return code in ("NoSuchKey", "404", "NotFound")


class S3BlobStore:
    """MinIO(로컬)/S3(운영) 공용. §4.2 S3 레이아웃을 그대로 key prefix 로 쓴다.

    boto3 는 동기 클라이언트라 LocalBlobStore 와 같은 방식으로 asyncio.to_thread 에 태운다.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            # path-style: MinIO 는 virtual-hosted-style(버킷.호스트) DNS 를 못 푼다
            config=Config(s3={"addressing_style": "path"}),
        )
        self._bucket_ready = False

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)
        self._bucket_ready = True

    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> str:
        def _put() -> None:
            self._ensure_bucket()
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )

        await asyncio.to_thread(_put)
        return key

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            self._ensure_bucket()
            try:
                obj = self._client.get_object(Bucket=self._bucket, Key=key)
                return obj["Body"].read()
            except ClientError as e:
                if _is_not_found(e):
                    raise BlobNotFound(key) from e
                raise

        return await asyncio.to_thread(_get)

    async def exists(self, key: str) -> bool:
        def _exists() -> bool:
            self._ensure_bucket()
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError as e:
                if _is_not_found(e):
                    return False
                raise

        return await asyncio.to_thread(_exists)

    async def presign(self, key: str, ttl: timedelta) -> str:
        if not await self.exists(key):
            raise BlobNotFound(key)

        def _presign() -> str:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=int(ttl.total_seconds()),
            )

        return await asyncio.to_thread(_presign)
