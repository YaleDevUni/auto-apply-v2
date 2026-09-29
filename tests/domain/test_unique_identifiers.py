"""주민등록번호는 Profile·AnswerKB·Experience 어느 필드에도 저장할 수 없다 (절대 규칙 5).

DTO 생성 자체가 실패해야 한다 — 그래야 저장소·API·에이전트 어느 경로로도 들어오지 못한다.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from auto_apply.contracts.experience import Experience, ExperienceFact, ExperienceSection
from auto_apply.contracts.knowledge import Answer, DocumentMeta
from auto_apply.contracts.profile import (
    AdditionalInfo,
    EducationEntry,
    MilitaryService,
    Profile,
    ProfileLink,
)
from auto_apply.domain.enums import ExperienceKind, MilitaryStatus
from auto_apply.domain.errors import UniqueIdentifierRejected
from auto_apply.domain.unique_identifiers import (
    contains_resident_registration_number,
    reject_unique_identifiers,
)

RRN = "900101-1234567"


def _fullwidth(text: str) -> str:
    """ASCII → 전각 (`0` → U+FF10, `-` → U+FF0D)."""
    return "".join(chr(ord(c) + 0xFEE0) for c in text)


_ZWSP, _ZWNJ, _ZWJ, _BOM = (chr(c) for c in (0x200B, 0x200C, 0x200D, 0xFEFF))
_EN_DASH = chr(0x2013)


@pytest.mark.parametrize(
    "text",
    [
        RRN,
        "9001011234567",
        "900101 - 2234567",
        "주민번호: 010315-4123456 입니다",
        f"901231{_EN_DASH}8123456",  # 외국인등록번호(뒷자리 5~8)
        "850101-9123456",  # 1800년대 출생 뒷자리 9
        "850101-0123456",  # 1800년대 출생 뒷자리 0
        "900101.1234567",
        "900101_1234567",
        "900101/1234567",
        _fullwidth("900101-1234567"),
        _fullwidth("9001011234567"),
        f"9001{_ZWSP}01-1234{_ZWNJ}567",
        f"900101{_ZWJ}-{_BOM}1234567",
    ],
)
def test_detects_resident_registration_numbers(text):
    assert contains_resident_registration_number(text)


@pytest.mark.parametrize(
    "text",
    [
        "010-1234-5678",  # 휴대폰
        "+82-10-1234-5678",
        "+82 10 1234 5678",
        "02-123-4567",
        "010.1234.5678",
        "2023.08 - 2024.04",  # 기간
        "2026-09-30",  # 날짜
        "2026.09.30",
        "2026/09/30",
        "20260930",
        "123-45-67890",  # 사업자등록번호
        "110-123-456789",  # 계좌
        "1002-345-678901",
        "1234-5678-9012-3456",  # 카드
        "1234567890123456",
        "11-12-345678-90",  # 운전면허
        "900101-1******",  # 뒷자리 가림
        "901301-1234567",  # 13월 — 생년월일 꼴이 아니다
        "12900101-12345678",  # 더 긴 숫자열의 일부
        "매출 1,234,567원",
        "",
    ],
)
def test_ignores_non_identifiers(text):
    assert not contains_resident_registration_number(text)


def test_reject_walks_nested_values_and_hides_the_value():
    with pytest.raises(UniqueIdentifierRejected) as exc:
        reject_unique_identifiers({"a": [{"b": ("x", RRN)}]}, where="X")
    assert RRN not in str(exc.value)
    assert "X" in str(exc.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": RRN},
        {"phone": RRN},
        {"email": f"{RRN}@example.com"},
        {"skills": [RRN]},
        {"links": [ProfileLink(label="x", url=f"https://x/{RRN}")]},
        {"education": [EducationEntry(school="x", period="y", note=RRN)]},
        {"additional": AdditionalInfo(residence=RRN)},
        {"additional": AdditionalInfo(desired_salary=RRN)},
        {
            "additional": AdditionalInfo(
                military=MilitaryService(status=MilitaryStatus.EXEMPT, note=RRN)
            )
        },
    ],
)
def test_profile_rejects_rrn_in_any_field(kwargs):
    with pytest.raises(ValidationError) as exc:
        Profile(**{"user_id": "u1", "name": "홍길동", **kwargs})
    assert "900101" not in str(exc.value)  # 거부한 번호가 에러 메시지로 새지 않는다


@pytest.mark.parametrize("field", ["question_key", "answer", "source_application_id"])
def test_answer_rejects_rrn_in_any_field(field):
    values = {
        "id": "a1",
        "user_id": "u1",
        "question_key": "resident_number",
        "answer": "알려드릴 수 없습니다",
        "source_application_id": "app_1",
        "updated_at": datetime(2026, 9, 30, tzinfo=UTC),
    }
    with pytest.raises(ValidationError):
        Answer(**{**values, field: RRN})


def test_experience_rejects_rrn_in_nested_fact():
    with pytest.raises(ValidationError):
        Experience(
            id="e1",
            user_id="u1",
            kind=ExperienceKind.COMPANY,
            name="Acme",
            sections=[
                ExperienceSection(key="k", title="t", facts=[ExperienceFact(id="f", text=RRN)])
            ],
        )


def test_document_meta_rejects_rrn_in_filename():
    with pytest.raises(ValidationError) as exc:
        DocumentMeta(
            id="d1",
            user_id="u1",
            filename=f"주민등록등본_{RRN}.pdf",
            content_type="application/pdf",
            size_bytes=1,
            blob_key="documents/d1.pdf",
            created_at=datetime(2026, 9, 30, tzinfo=UTC),
        )
    assert "900101" not in str(exc.value)


def test_profile_without_identifiers_is_accepted():
    profile = Profile(user_id="u1", name="홍길동", phone="010-1234-5678")
    assert profile.additional == AdditionalInfo()
