"""이력서 파일 바이트 → `ProfileExtraction` (§A7 온보딩 추출). 초안 저장은 profile_drafts.py.

주민등록번호 꼴은 LLM 에 보내기 전에 메모리에서 가린다(절대 규칙 5) — 원문·가린 값·위치는 저장도
로그도 하지 않고 개수만 돌려준다. 업로드 고정 파일(services/uploads.py)은 거부하는데 여기는 가리는
이유: 추출은 원본을 제출하지 않고 초안의 재료로만 쓰므로 내용을 바꿔도 되고, 사용자가 번호가 든
이력서로도 온보딩할 수 있어야 한다.
"""

from auto_apply.ai.profile_extraction import ProfileExtraction, build_profile_extraction_prompt
from auto_apply.ai.prompts import reprompt_error_suffix
from auto_apply.domain.errors import InvalidInput, LLMSchemaViolation
from auto_apply.domain.unique_identifiers import redact_resident_registration_numbers
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.text_extract import DocumentTextExtractor


async def extract_profile(
    extractor: DocumentTextExtractor,
    llm: LLMClient,
    data: bytes,
    content_type: str,
    *,
    max_text_chars: int,
    max_reprompts: int,
) -> tuple[ProfileExtraction, int]:
    """(추출 결과, 가린 주민등록번호 꼴 개수)."""
    text, redacted = redact_resident_registration_numbers(
        await extractor.extract_text(data, content_type)
    )
    if len(text) > max_text_chars:
        raise InvalidInput(f"이력서 글자 수가 {max_text_chars}자 상한을 넘는다")
    prompt = build_profile_extraction_prompt(text)
    # 원본을 cache_prefix 로 고정하고 재시도마다 오류 안내만 바꾼다
    # (adapters/resume/simple.py 와 같은 방식).
    addition = ""
    for attempt in range(max_reprompts + 1):
        try:
            content = await llm.structured(
                addition, ProfileExtraction, max_tokens=8192, cache_prefix=prompt
            )
            return content, redacted
        except LLMSchemaViolation as e:
            if attempt == max_reprompts:
                raise
            addition = reprompt_error_suffix(str(e))
    raise AssertionError("unreachable")
