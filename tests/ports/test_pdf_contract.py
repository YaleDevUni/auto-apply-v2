"""PdfRenderer contract test. weasyprint 는 cairo/pango/glib 시스템 라이브러리가 필요해서
integration 마크 — `make test-all`에서만 돈다(§11.1 원칙 4, "인프라 필요하면 integration")."""

import pytest

from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import ResumeDraft

DRAFT = ResumeDraft(
    resume_id="r1",
    content={"name": "테스터", "summary": "충분히 긴 요약 문장입니다", "highlights": []},
)


async def test_stub_renderer_writes_json_blob():
    store = InMemoryBlobStore()
    result = await StubPdfRenderer(store).render(DRAFT)
    assert result.blob_key == "resumes/r1.json"
    blob = await store.get(result.blob_key)
    assert b"\xed\x85\x8c\xec\x8a\xa4\xed\x84\xb0" in blob  # "테스터" UTF-8


@pytest.mark.integration
async def test_weasyprint_renderer_writes_a_real_pdf():
    from auto_apply.adapters.pdf.weasyprint import WeasyPrintPdfRenderer

    store = InMemoryBlobStore()
    result = await WeasyPrintPdfRenderer(store).render(DRAFT)
    # 채용담당자가 파일 목록에서 이력서/포트폴리오를 구별할 수 있게 사람이 읽을 수 있는
    # 이름으로 짓는다 (domain.resume_cleanup.build_resume_filename).
    assert result.blob_key == "resumes/테스터_이력서_r1.pdf"
    blob = await store.get(result.blob_key)
    assert blob.startswith(b"%PDF")
    assert result.bytes_written == len(blob)
