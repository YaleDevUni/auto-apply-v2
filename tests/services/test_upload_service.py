"""UploadService — 바이트·메타를 함께 다루고 고아 파일을 남기지 않는다 (§A7)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_apply.adapters.extract.pdf_docx import PdfDocxTextExtractor
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.domain.enums import DocumentKind, ExperienceKind
from auto_apply.domain.errors import NotFound, UniqueIdentifierRejected
from auto_apply.services.profile import ProfileService
from auto_apply.services.uploads import UploadService
from tests.documents import blank_pdf, make_docx
from tests.services.fakes import FixedClock, SeqIds

PDF = b"%PDF-1.7\n..."
RRN = "900101-1234567"


@pytest.fixture
def store() -> InMemoryBlobStore:
    return InMemoryBlobStore()


@pytest.fixture
def svc(uow_factory, store) -> UploadService:
    return UploadService(
        uow_factory, store, PdfDocxTextExtractor(), FixedClock(), SeqIds(), max_document_bytes=64
    )


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

    svc = UploadService(_BrokenUow, store, PdfDocxTextExtractor(), FixedClock(), SeqIds())
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


# ── 고정 파일 본문의 주민등록번호 (절대 규칙 5) — 가리지 않고 거부한다 ──────────


@pytest.fixture
def big_svc(uow_factory, store) -> UploadService:
    return UploadService(uow_factory, store, PdfDocxTextExtractor(), FixedClock(), SeqIds())


@pytest.mark.parametrize(
    "line",
    [
        f"주민등록번호 {RRN}",
        "주민번호: " + "".join(chr(ord(c) + 0xFEE0) for c in RRN),
        "9001​01 1234567",
    ],
    ids=["plain", "fullwidth", "zero-width"],
)
async def test_document_text_with_rrn_rejected_before_bytes(big_svc, store, line):
    with pytest.raises(UniqueIdentifierRejected) as exc:
        await big_svc.upload_document("u1", "resume.docx", make_docx(["김가상", line]))
    assert "1234567" not in str(exc.value)
    assert store._blobs == {}
    assert await big_svc.list_documents("u1") == []


async def test_document_text_without_rrn_is_stored(big_svc):
    pdf = (Path(__file__).parents[1] / "fixtures" / "resumes" / "dev_resume.pdf").read_bytes()
    assert (await big_svc.upload_document("u1", "resume.pdf", pdf)).size_bytes == len(pdf)
    masked = make_docx(["주민번호 900101-1******"])  # 뒷자리를 가린 값은 번호가 아니다
    assert (await big_svc.upload_document("u1", "masked.docx", masked)).filename == "masked.docx"


async def test_files_without_text_are_accepted_unchecked(big_svc):
    """이미지·글자 없는 PDF 는 검사할 수 없어 그대로 받는다 (§A7 한계)."""
    await big_svc.upload_document("u1", "photo.png", b"\x89PNG\r\n\x1a\n" + RRN.encode())
    await big_svc.upload_document("u1", "scan.pdf", blank_pdf())
    assert len(await big_svc.list_documents("u1")) == 2
