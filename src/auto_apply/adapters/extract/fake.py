from collections.abc import Mapping

from auto_apply.domain.errors import TextExtractionFailed
from auto_apply.ports.text_extract import DOCX_CONTENT_TYPE, PDF_CONTENT_TYPE

# 형식과 바이트가 어긋난 입력은 실제 구현처럼 "읽을 수 없는 파일"로 본다.
_SIGNATURE = {PDF_CONTENT_TYPE: b"%PDF-", DOCX_CONTENT_TYPE: b"PK\x03\x04"}


class FakeTextExtractor:
    """테스트 대역 — 미리 등록한 바이트에 등록한 텍스트를 돌려준다.

    등록 안 된 바이트는 "읽을 수 없는 파일"로 본다(계약의 `TextExtractionFailed`). 실제 파서 없이
    서비스·API 테스트가 추출 결과를 고정할 수 있게 한다.
    """

    def __init__(self, texts: Mapping[bytes, str]) -> None:
        self._texts = dict(texts)

    async def extract_text(self, data: bytes, content_type: str) -> str:
        signature = _SIGNATURE.get(content_type)
        if signature is None:
            raise TextExtractionFailed("PDF·DOCX 만 글자를 뽑을 수 있다")
        if not data.startswith(signature):
            raise TextExtractionFailed("파일을 읽을 수 없다")
        text = self._texts.get(data, "").strip()
        if not text:
            raise TextExtractionFailed("문서에 글자가 없다")
        return text
