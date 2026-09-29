"""고유식별정보 거부 — 저장 경로에 주민등록번호가 들어오지 못하게 한다 (00-product 절대 규칙 5).

두 겹으로 막는다: 프로필·답변KB·경험·문서 메타 DTO 가 생성 시점에(contracts/_base.py), 그리고
repository 가 저장 시점에(`model_copy(update=...)`·`model_construct` 는 검증기를 건너뛴다) 이
규칙을 부른다.

외국인등록번호도 형식이 같아 같은 패턴에 걸린다. 뒷자리 첫 수는 0~9 전부(1800년대 출생 9·0 포함).
뒷자리를 가린 값(`900101-1******`)은 번호 전체가 아니라 통과시킨다.
"""

import re
import unicodedata
from collections.abc import Iterator, Mapping

from auto_apply.domain.errors import UniqueIdentifierRejected

# NFKC 가 전각 숫자·전각 하이픈(U+FF10~U+FF19, U+FF0D)을 반각으로 접는다. zero-width 문자는 NFKC 가
# 지우지 않아 따로 뺀다 — 숫자 사이에 끼워 넣어 패턴을 피하는 값을 막으려는 것.
_ZERO_WIDTH: dict[int, None] = dict.fromkeys((0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))

# 앞 6자리는 실제 생년월일 꼴(월 01~12, 일 01~31)만 — 아무 13자리 숫자나 거부하지 않게.
# 구분자는 하이픈 계열·`.`·`_`·`/`·공백 중 하나 또는 없음. 앞뒤가 숫자로 이어지면(더 긴
# 숫자열의 일부) 걸지 않는다. re 가 \u 이스케이프를 해석한다.
_RRN = re.compile(
    r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
    r"\s*[-\u2010-\u2015\u2212._/]?\s*\d{7}(?!\d)"
)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)


def contains_resident_registration_number(text: str) -> bool:
    return _RRN.search(_normalize(text)) is not None


# 가린 자리 표시. 숫자가 없어 다시 패턴에 걸리지 않는다.
REDACTION_MARK = "[주민등록번호 가림]"


def redact_resident_registration_numbers(text: str) -> tuple[str, int]:
    """(가린 텍스트, 가린 개수). 탐지 규칙은 거부와 같다(NFKC·zero-width·구분자).

    가린 게 있으면 텍스트 전체를 정규화한 형태로 돌려준다 — 전각·zero-width 로 쪼갠 번호도
    같은 위치에서 가려야 해서다. 없으면 원문 그대로. 가린 값·위치는 어디에도 남기지 않는다.
    """
    normalized = _normalize(text)
    redacted, count = _RRN.subn(REDACTION_MARK, normalized)
    return (redacted, count) if count else (text, 0)


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list | tuple | set | frozenset):
        for v in value:
            yield from _strings(v)


def reject_unique_identifiers(value: object, *, where: str) -> None:
    """`value`(문자열·dict·list 중첩) 안 어느 문자열에든 주민등록번호가 있으면 거부한다.

    메시지에는 어디(`where`)였는지만 쓴다 — 번호를 로그·에러 응답으로 흘리지 않는다.
    """
    if any(contains_resident_registration_number(s) for s in _strings(value)):
        raise UniqueIdentifierRejected(
            f"{where}: 주민등록번호 형식의 값은 저장할 수 없다 (고유식별정보, 절대 규칙 5)"
        )
