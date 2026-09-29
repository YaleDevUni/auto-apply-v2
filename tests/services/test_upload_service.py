"""UploadService — 바이트·메타를 함께 다루고 고아 파일을 남기지 않는다 (§A7)."""

import pytest
from pydantic import ValidationError

from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.domain.enums import DocumentKind, ExperienceKind
from auto_apply.domain.errors import NotFound
from auto_apply.services.profile import ProfileService
from auto_apply.services.uploads import UploadService
from tests.services.fakes import FixedClock, SeqIds

PDF = b"%PDF-1.7\n..."
RRN = "900101-1234567"


@pytest.fixture
def store() -> InMemoryBlobStore:
    return InMemoryBlobStore()


@pytest.fixture
def svc(uow_factory, store) -> UploadService:
    return UploadService(uow_factory, store, FixedClock(), SeqIds(), max_document_bytes=64)


async def test_upload_stores_bytes_and_meta(svc, store):
    meta = await svc.upload_document("u1", "C:\\fakepath\\이력서.pdf", PDF)
    assert meta.kind is DocumentKind.UPLOADED
    assert meta.filename == "이력서.pdf"
    assert meta.size_bytes == len(PDF)
    assert "이력서" not in meta.blob_key
    assert await svc.read_document("u1", meta.id) == (meta, PDF)
    assert await svc.list_documents("u1") == [meta]
    assert list(store._blobs) == [meta.blob_key]
    with pytest.raises(NotFound):
        await svc.get_document("u2", meta.id)


async def test_rrn_filename_rejected_before_bytes(svc, store):
    with pytest.raises(ValidationError):
        await svc.upload_document("u1", f"{RRN}.pdf", PDF)
    assert store._blobs == {}


async def test_failed_meta_save_removes_bytes(store):
    class _FailingDocs:
        async def save(self, _meta):
            raise RuntimeError("db down")

    class _BrokenUow:
        documents = _FailingDocs()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def commit(self):
            return None

    svc = UploadService(_BrokenUow, store, FixedClock(), SeqIds())
    with pytest.raises(RuntimeError):
        await svc.upload_document("u1", "a.pdf", PDF)
    assert store._blobs == {}  # 메타 없는 바이트를 남기지 않는다


async def test_delete_removes_bytes_meta_and_references(uow_factory, svc, store):
    profiles = ProfileService(uow_factory, FixedClock(), SeqIds())
    doc = await svc.upload_document("u1", "a.pdf", PDF)
    keep = await svc.upload_document("u1", "b.pdf", PDF)
    exp = await profiles.create_experience(
        "u1",
        {"kind": ExperienceKind.PROJECT, "name": "p", "document_ids": [doc.id, keep.id]},
    )
    await svc.delete_document("u1", doc.id)
    assert await svc.list_documents("u1") == [keep]
    assert (await profiles.get_experience("u1", exp.id)).document_ids == [keep.id]
    assert list(store._blobs) == [keep.blob_key]  # 고아 파일 0
    with pytest.raises(NotFound):
        await svc.delete_document("u1", doc.id)


async def test_delete_other_users_document_is_not_found(svc, store):
    doc = await svc.upload_document("u1", "a.pdf", PDF)
    with pytest.raises(NotFound):
        await svc.delete_document("u2", doc.id)
    assert await store.exists(doc.blob_key)
