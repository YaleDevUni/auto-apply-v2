"""BlobStore contract test (ARCHITECTURE.md §11.5).

같은 스위트를 모든 구현에 돌린다. 새 어댑터를 추가하면 params 에 이름만 넣는다.
port 에서 새는 것은 보통 반환값이 아니라 '예외' 라서, 예외 계약을 여기서 못박는다.
"""

from datetime import timedelta

import boto3
import pytest
from botocore.client import Config
from botocore.exceptions import ClientError

from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.adapters.storage.s3 import S3BlobStore
from auto_apply.config import Settings
from auto_apply.domain.errors import BlobNotFound
from auto_apply.ports.storage import BlobStore


@pytest.fixture(params=["memory", "local", pytest.param("s3", marks=pytest.mark.docker)])
def store(request: pytest.FixtureRequest, tmp_path) -> BlobStore:
    if request.param == "memory":
        return InMemoryBlobStore()
    if request.param == "local":
        return LocalBlobStore(tmp_path)
    # s3 — `make up`이 띄운 MinIO. 전용 test 버킷을 매 테스트 전에 비운다(운영 s3_bucket과
    # 분리하는 이유는 postgres-integration-test-data-wipe-hazard와 동일).
    cfg = Settings()
    assert cfg.s3_test_bucket != cfg.s3_bucket, (
        "S3_TEST_BUCKET 이 S3_BUCKET 과 같다 — 이 fixture 는 매 테스트 전에 버킷을 통째로"
        " 비우므로 운영 버킷을 그대로 가리키면 실제 데이터가 지워진다."
    )
    client = boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint_url,
        aws_access_key_id=cfg.s3_access_key or None,
        aws_secret_access_key=cfg.s3_secret_key or None,
        config=Config(s3={"addressing_style": "path"}),
    )
    try:
        client.head_bucket(Bucket=cfg.s3_test_bucket)
    except ClientError:
        client.create_bucket(Bucket=cfg.s3_test_bucket)
    objs = client.list_objects_v2(Bucket=cfg.s3_test_bucket).get("Contents", [])
    if objs:
        client.delete_objects(
            Bucket=cfg.s3_test_bucket,
            Delete={"Objects": [{"Key": o["Key"]} for o in objs]},
        )
    return S3BlobStore(
        endpoint_url=cfg.s3_endpoint_url,
        bucket=cfg.s3_test_bucket,
        access_key=cfg.s3_access_key,
        secret_key=cfg.s3_secret_key,
    )


async def test_put_then_get_roundtrip(store: BlobStore):
    await store.put("resumes/u1/r1.pdf", b"%PDF-1.7", content_type="application/pdf")
    assert await store.get("resumes/u1/r1.pdf") == b"%PDF-1.7"


async def test_put_is_idempotent_overwrite(store: BlobStore):
    await store.put("a/b.txt", b"first")
    await store.put("a/b.txt", b"second")
    assert await store.get("a/b.txt") == b"second"


async def test_exists_reflects_state(store: BlobStore):
    assert await store.exists("missing.txt") is False
    await store.put("missing.txt", b"x")
    assert await store.exists("missing.txt") is True


async def test_get_missing_raises_blob_not_found(store: BlobStore):
    with pytest.raises(BlobNotFound):
        await store.get("nope/nothing.bin")


async def test_presign_missing_raises_blob_not_found(store: BlobStore):
    with pytest.raises(BlobNotFound):
        await store.presign("nope/nothing.bin", timedelta(minutes=5))


async def test_presign_returns_url_for_existing_key(store: BlobStore):
    await store.put("dom-snapshots/wanted/abc/1.html", b"<html/>")
    url = await store.presign("dom-snapshots/wanted/abc/1.html", timedelta(minutes=5))
    assert url and "://" in url


async def test_nested_keys_are_supported(store: BlobStore):
    key = "application-artifacts/app_1/1/step-03.png"
    await store.put(key, b"\x89PNG")
    assert await store.get(key) == b"\x89PNG"
