"""PdfRenderer contract test. 실제 구현(Chrome `page.pdf()`, §A7)이 붙으면 params 로 추가한다."""

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
