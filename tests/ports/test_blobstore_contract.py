"""BlobStore contract test (§A2).

같은 스위트를 모든 구현에 돌린다. 새 어댑터를 추가하면 params 에 이름만 넣는다.
port 에서 새는 것은 보통 반환값이 아니라 '예외' 라서, 예외 계약을 여기서 못박는다.
"""

from datetime import timedelta

import pytest

from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.domain.errors import BlobNotFound
from auto_apply.ports.storage import BlobStore


@pytest.fixture(params=["memory", "local"])
def store(request: pytest.FixtureRequest, tmp_path) -> BlobStore:
    if request.param == "memory":
        return InMemoryBlobStore()
    return LocalBlobStore(tmp_path)


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
    await store.put("runs/run_1/page.html", b"<html/>")
    url = await store.presign("runs/run_1/page.html", timedelta(minutes=5))
    assert url and "://" in url


async def test_nested_keys_are_supported(store: BlobStore):
    key = "application-artifacts/app_1/1/step-03.png"
    await store.put(key, b"\x89PNG")
    assert await store.get(key) == b"\x89PNG"


async def test_delete_removes_and_reports(store: BlobStore):
    await store.put("documents/u1/d1.pdf", b"%PDF-")
    assert await store.delete("documents/u1/d1.pdf") is True
    assert await store.exists("documents/u1/d1.pdf") is False
    with pytest.raises(BlobNotFound):
        await store.get("documents/u1/d1.pdf")


async def test_delete_missing_is_false_not_error(store: BlobStore):
    assert await store.delete("nope/nothing.bin") is False


async def test_delete_leaves_other_keys(store: BlobStore):
    await store.put("a/1.bin", b"1")
    await store.put("a/2.bin", b"2")
    await store.delete("a/1.bin")
    assert await store.get("a/2.bin") == b"2"


async def test_local_delete_cannot_escape_root(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep")
    store = LocalBlobStore(tmp_path / "root")
    with pytest.raises(ValueError):
        await store.delete("../outside.txt")
    assert outside.read_bytes() == b"keep"


async def test_list_keys_under_prefix(store: BlobStore):
    for key in ["drafts/u1/b.json", "drafts/u1/a.json", "drafts/u1/sub/c.json", "drafts/u2/x.json"]:
        await store.put(key, b"{}")
    await store.put("drafts/u1x.json", b"{}")  # 이름만 겹치는 이웃은 포함되지 않는다
    assert await store.list_keys("drafts/u1/") == [
        "drafts/u1/a.json",
        "drafts/u1/b.json",
        "drafts/u1/sub/c.json",
    ]
    assert await store.list_keys("nothing/") == []
    await store.delete("drafts/u1/a.json")
    assert "drafts/u1/a.json" not in await store.list_keys("drafts/u1/")


async def test_list_keys_needs_directory_prefix(store: BlobStore):
    with pytest.raises(ValueError):
        await store.list_keys("drafts/u1")


async def test_local_list_keys_cannot_escape_root(tmp_path):
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_bytes(b"keep")
    store = LocalBlobStore(tmp_path / "root")
    with pytest.raises(ValueError):
        await store.list_keys("../outside/")
