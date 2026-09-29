"""PDF(pypdf)·DOCX(zip + WordprocessingML) 텍스트 추출 — DocumentTextExtractor 실제 구현 (§A7).

입력은 사용자가 올린 파일이라 악성일 수 있다. pypdf 는 자체 압축 해제 상한이 있고, DOCX 는
zip 폭탄(선언 크기·실제 읽기 둘 다 상한)과 XML 엔티티 확장(DOCTYPE 거부)을 여기서 막는다.
파싱은 CPU 작업이라 스레드로 넘긴다.
"""

import asyncio
import io
import zipfile
from xml.etree import ElementTree

from pypdf import PdfReader

from auto_apply.domain.errors import TextExtractionFailed
from auto_apply.ports.text_extract import DOCX_CONTENT_TYPE, PDF_CONTENT_TYPE

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCX_BODY = "word/document.xml"


class PdfDocxTextExtractor:
    def __init__(self, *, max_pages: int = 30, max_xml_bytes: int = 20 * 1024 * 1024) -> None:
        self._max_pages = max_pages
        self._max_xml_bytes = max_xml_bytes

    async def extract_text(self, data: bytes, content_type: str) -> str:
        if content_type == PDF_CONTENT_TYPE:
            text = await asyncio.to_thread(self._pdf, data)
        elif content_type == DOCX_CONTENT_TYPE:
            text = await asyncio.to_thread(self._docx, data)
        else:
            raise TextExtractionFailed("PDF·DOCX 만 글자를 뽑을 수 있다")
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if not text:
            raise TextExtractionFailed("문서에 글자가 없다 — 스캔 이미지 PDF 는 지원하지 않는다")
        return text

    def _pdf(self, data: bytes) -> str:
        try:
            reader = PdfReader(io.BytesIO(data))
            # 소유자 암호만 걸린(열람 암호 없는) PDF 는 빈 암호로 열린다.
            if reader.is_encrypted and not reader.decrypt(""):
                raise TextExtractionFailed("암호가 걸린 PDF 는 읽을 수 없다")
            if len(reader.pages) > self._max_pages:
                raise TextExtractionFailed(f"PDF 가 {self._max_pages}쪽을 넘는다")
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except TextExtractionFailed:
            raise
        except Exception as e:
            # pypdf 는 망가진 입력에 여러 종류의 예외를 던진다 — 경계에서 계약 예외 하나로 접는다.
            raise TextExtractionFailed("PDF 를 읽을 수 없다 (파일이 손상됐을 수 있다)") from e

    def _docx(self, data: bytes) -> str:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                info = zf.getinfo(_DOCX_BODY)
                if info.file_size > self._max_xml_bytes:
                    raise TextExtractionFailed("DOCX 본문이 너무 크다")
                with zf.open(info) as f:
                    # 선언 크기를 속인 항목도 상한까지만 읽는다.
                    xml = f.read(self._max_xml_bytes + 1)
        except TextExtractionFailed:
            raise
        except (zipfile.BadZipFile, KeyError, OSError, EOFError, ValueError) as e:
            raise TextExtractionFailed("DOCX 를 읽을 수 없다 (파일이 손상됐을 수 있다)") from e
        if len(xml) > self._max_xml_bytes:
            raise TextExtractionFailed("DOCX 본문이 너무 크다")
        # 워드가 만든 본문에는 DOCTYPE 이 없다. 있으면 엔티티 확장 공격으로 보고 파싱하지 않는다.
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise TextExtractionFailed("DOCX 본문 형식이 올바르지 않다")
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as e:
            raise TextExtractionFailed("DOCX 본문 형식이 올바르지 않다") from e
        return "\n".join(_paragraph_text(p) for p in root.iter(f"{_W}p"))


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for el in paragraph.iter():
        if el.tag == f"{_W}t":
            parts.append(el.text or "")
        elif el.tag == f"{_W}tab":
            parts.append("\t")
        elif el.tag in {f"{_W}br", f"{_W}cr"}:
            parts.append("\n")
    return "".join(parts)
