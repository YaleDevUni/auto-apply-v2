"""온보딩 초안 모델 (§A7). 저장·API 가 같이 쓴다 — 저장 DTO 가 아니라 서비스 계층 모델이다."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field

from auto_apply.ai.profile_extraction import ProfileExtraction
from auto_apply.contracts._base import Frozen, IdentifierFree
from auto_apply.contracts.experience import Experience
from auto_apply.contracts.profile import Profile


class ProfileField(StrEnum):
    NAME = "name"
    PHONE = "phone"
    EMAIL = "email"
    LINKS = "links"
    EDUCATION = "education"
    SKILLS = "skills"
    LANGUAGES = "languages"
    ADDITIONAL = "additional"


class DraftSource(StrEnum):
    RESUME = "resume"  # 업로드한 이력서 파일 → LLM 추출
    V2_YAML = "v2_yaml"  # v2 config/*.yaml → 결정론 변환


class ProfileDraft(IdentifierFree):
    id: str
    user_id: str
    source: DraftSource
    document_id: str | None = None  # 저장된 문서에서 추출했을 때 그 문서
    source_filename: str | None = None  # 추출한 이력서 파일 이름 (표시용)
    # LLM 에 보내기 전에 가린 주민등록번호 꼴 개수 — 값·위치는 남기지 않는다 (절대 규칙 5).
    redacted_identifiers: int = Field(default=0, ge=0)
    created_at: AwareDatetime
    content: ProfileExtraction


class DraftSummary(Frozen):
    """초안 목록 한 줄 — 새로고침 뒤 이어서 검토할 초안을 고르는 용도."""

    id: str
    source: DraftSource
    source_filename: str | None
    document_id: str | None
    created_at: AwareDatetime
    profile_field_count: int  # 값이 있는 인적사항 필드 수
    experience_count: int
    redacted_identifiers: int

    @classmethod
    def of(cls, draft: ProfileDraft) -> Self:
        profile = draft.content.profile
        filled = [
            getattr(profile, f.value) for f in ProfileField if f is not ProfileField.ADDITIONAL
        ]
        additional = any(v is not None for v in profile.additional.model_dump().values())
        return cls(
            id=draft.id,
            source=draft.source,
            source_filename=draft.source_filename,
            document_id=draft.document_id,
            created_at=draft.created_at,
            profile_field_count=sum(1 for v in filled if v) + int(additional),
            experience_count=len(draft.content.experiences),
            redacted_identifiers=draft.redacted_identifiers,
        )


class DraftSelection(Frozen):
    """확정할 항목. 고르지 않은 것은 버린다."""

    profile_fields: list[ProfileField] = Field(default_factory=list)
    experience_indexes: list[int] = Field(default_factory=list)  # content.experiences 의 위치


class DraftConfirmation(Frozen):
    profile: Profile | None  # 인적사항 필드를 하나도 안 골랐으면 None (건드리지 않음)
    experiences: list[Experience]
