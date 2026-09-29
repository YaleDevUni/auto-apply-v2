"""DocumentTextExtractor contract test (§A2) — 실제(pypdf·zip) 구현과 대역에 같은 기대를 건다.

대역은 등록한 바이트만 읽으므로 픽스처 바이트→원문을 등록해 두고, 실제 구현은 파일을 진짜로
파싱한다.
"""

from pathlib import Path

import pytest

from auto_apply.adapters.extract.fake import FakeTextExtractor
from auto_apply.adapters.extract.pdf_docx import PdfDocxTextExtractor
from auto_apply.domain.errors import TextExtractionFailed
from auto_apply.domain.uploads import validate_upload
from auto_apply.ports.text_extract import (
    DOCX_CONTENT_TYPE,
    PDF_CONTENT_TYPE,
    DocumentTextExtractor,
)
from tests.documents import W_NS, blank_pdf, make_docx

RESUMES = Path(__file__).parents[1] / "fixtures" / "resumes"
DEV_PDF = (RESUMES / "dev_resume.pdf").read_bytes()
NONDEV_PDF = (RESUMES / "nondev_resume.pdf").read_bytes()


def test_content_types_match_upload_rules():
    """추출기가 받는 content type 은 업로드 때 서버가 정한 값이다 — 어긋나면 추출이 막힌다."""
    assert validate_upload("a.pdf", DEV_PDF, max_bytes=10**7)[1] == PDF_CONTENT_TYPE
    assert validate_upload("a.docx", make_docx(["x"]), max_bytes=10**7)[1] == DOCX_CONTENT_TYPE


DOCX = make_docx(["박샘플", "가상물류 — 운영 매니저", "배송 지연률을 12%에서 4%로 낮췄다."])


@pytest.fixture(params=["library", "fake"])
def extractor(request) -> DocumentTextExtractor:
    if request.param == "library":
        return PdfDocxTextExtractor(max_pages=5, max_xml_bytes=64 * 1024)
    return FakeTextExtractor(
        {
            DEV_PDF: "김가상\n백엔드 개발자\nFastAPI 기반 결제 승인 API",
            NONDEV_PDF: "이예시\n콘텐츠 마케터\n가상커머스",
            DOCX: "박샘플\n가상물류 — 운영 매니저\n배송 지연률을 12%에서 4%로 낮췄다.",
        }
    )


@pytest.mark.parametrize(
    ("data", "expected"),
    [(DEV_PDF, ["김가상", "FastAPI"]), (NONDEV_PDF, ["이예시", "가상커머스"])],
    ids=["dev", "nondev"],
)
async def test_pdf_text(extractor, data, expected):
    text = await extractor.extract_text(data, PDF_CONTENT_TYPE)
    assert all(word in text for word in expected)
    assert text == text.strip()


async def test_docx_text_keeps_paragraph_lines(extractor):
    text = await extractor.extract_text(DOCX, DOCX_CONTENT_TYPE)
    assert text.splitlines() == [
        "박샘플",
        "가상물류 — 운영 매니저",
        "배송 지연률을 12%에서 4%로 낮췄다.",
    ]


@pytest.mark.parametrize("content_type", ["image/png", "text/plain", ""])
async def test_other_types_rejected(extractor, content_type):
    with pytest.raises(TextExtractionFailed):
        await extractor.extract_text(DEV_PDF, content_type)


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (b"%PDF-1.7\nnot really a pdf", PDF_CONTENT_TYPE),
        (b"PK\x03\x04broken zip", DOCX_CONTENT_TYPE),
        (DEV_PDF, DOCX_CONTENT_TYPE),  # 형식과 바이트가 어긋남
        (b"", PDF_CONTENT_TYPE),
    ],
    ids=["broken-pdf", "broken-docx", "mismatch", "empty"],
)
async def test_broken_files_rejected(extractor, data, content_type):
    with pytest.raises(TextExtractionFailed):
        await extractor.extract_text(data, content_type)


async def test_no_text_is_an_error_not_empty_string(extractor):
    """스캔 이미지 PDF 처럼 글자가 없으면 빈 문자열 대신 에러 (계약)."""
    with pytest.raises(TextExtractionFailed):
        await extractor.extract_text(blank_pdf(), PDF_CONTENT_TYPE)


# ── 실제 구현 전용: 악성 입력 상한 ────────────────────────────────────────────


async def test_docx_entity_expansion_refused():
    lol = (
        '<?xml version="1.0"?><!DOCTYPE w [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    with pytest.raises(TextExtractionFailed):
        await PdfDocxTextExtractor().extract_text(make_docx([], body=lol), DOCX_CONTENT_TYPE)


async def test_docx_oversized_body_refused():
    big = make_docx(["가" * 40_000])  # UTF-8 로 120 KB — 압축하면 작지만 풀면 상한을 넘는다
    assert len(big) < 64 * 1024
    with pytest.raises(TextExtractionFailed):
        await PdfDocxTextExtractor(max_xml_bytes=64 * 1024).extract_text(big, DOCX_CONTENT_TYPE)


async def test_pdf_page_limit():
    with pytest.raises(TextExtractionFailed):
        await PdfDocxTextExtractor(max_pages=0).extract_text(DEV_PDF, PDF_CONTENT_TYPE)
