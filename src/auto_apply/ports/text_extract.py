from typing import Protocol

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class DocumentTextExtractor(Protocol):
    """이력서 파일(PDF·DOCX) 바이트 → 평문 (§A7 온보딩 추출).

    계약:
    - `content_type` 은 업로드 때 서버가 정한 값(`domain/uploads.py`)이다. PDF·DOCX 가 아니면
      `TextExtractionFailed`.
    - 망가진 파일·암호 걸린 파일·글자가 하나도 없는 파일(스캔 이미지)·구현별 상한 초과도
      `TextExtractionFailed` — 빈 문자열을 돌려주지 않는다.
    - 돌려주는 텍스트는 앞뒤 공백을 뗀 문단 단위 줄바꿈 텍스트다. 레이아웃 복원은 약속하지 않는다.
    """

    async def extract_text(self, data: bytes, content_type: str) -> str: ...
