"""요청 본문 스키마. 저장 DTO 에서 서버가 정하는 값(user_id·id·시각)을 뺀 모양이다.

`IdentifierFree` 를 상속해 주민등록번호가 든 본문은 요청 검증 단계에서 422 로 떨어진다 —
서비스·저장소 검사(절대 규칙 5)에 앞선 첫 겹.
"""

from pydantic import Field

from auto_apply.contracts._base import Frozen, IdentifierFree
from auto_apply.contracts.profile import (
    AdditionalInfo,
    EducationEntry,
    LanguageEntry,
    Profile,
    ProfileLink,
)
from auto_apply.domain.enums import ExperienceKind


class ProfileBody(IdentifierFree):
    """`Profile` 에서 user_id 만 뺐다 (필드 일치는 테스트가 확인)."""

    name: str
    phone: str = ""
    email: str = ""
    links: list[ProfileLink] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    languages: list[LanguageEntry] = Field(default_factory=list)
    additional: AdditionalInfo = Field(default_factory=AdditionalInfo)

    def to_profile(self, user_id: str) -> Profile:
        return Profile.model_validate({**self.model_dump(), "user_id": user_id})


class ExperienceFactBody(Frozen):
    id: str | None = None  # 비우면 서버가 발급한다. 기존 fact 를 고칠 때는 그 id 를 보낸다.
    text: str
    skills: list[str] = Field(default_factory=list)


class ExperienceSectionBody(Frozen):
    key: str
    title: str
    period: str = ""
    facts: list[ExperienceFactBody] = Field(default_factory=list)


class ExperienceBody(IdentifierFree):
    kind: ExperienceKind
    name: str
    role: str = ""
    period: str = ""
    url: str | None = None
    skills: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    facts: list[ExperienceFactBody] = Field(default_factory=list)
    sections: list[ExperienceSectionBody] = Field(default_factory=list)


class AnswerBody(IdentifierFree):
    question: str  # 원문 그대로 — 서버가 질문 키로 정규화한다
    answer: str
    source_application_id: str | None = None
