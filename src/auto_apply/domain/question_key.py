"""답변KB 질문 키 정규화 (§A7) — 사이트마다 조금씩 다른 같은 문항을 한 키로 모은다."""

import re
import unicodedata

from auto_apply.domain.errors import InvalidInput

_ZERO_WIDTH: dict[int, None] = dict.fromkeys((0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
_SPACES = re.compile(r"\s+")


def normalize_question_key(question: str) -> str:
    """ "희망 연봉은?*", "희망  연봉은" → "희망 연봉은".

    공백은 지우지 않고 하나로만 접는다 — Answer 에 질문 원문 필드가 따로 없어 키가 곧 화면 표시다.
    앞뒤의 문장부호·기호(필수 표시 `*`, 물음표, 콜론)는 문항 의미가 아니라 양식이라 떼어낸다.
    """
    text = unicodedata.normalize("NFKC", question).translate(_ZERO_WIDTH).casefold()
    text = _SPACES.sub(" ", text)
    start, end = 0, len(text)
    while start < end and _is_trim_char(text[start]):
        start += 1
    while end > start and _is_trim_char(text[end - 1]):
        end -= 1
    key = text[start:end]
    if not key:
        raise InvalidInput("질문이 비어 있다")
    return key


def _is_trim_char(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch)[0] in {"P", "S"}
