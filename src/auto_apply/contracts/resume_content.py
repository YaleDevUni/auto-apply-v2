"""조립된 이력서 최종 콘텐츠 — `ResumeDraft.content`에 담기는 실제 모양(§A7).

`SimpleResumeGenerator`가 결정론적 블록 메타데이터(회사명·기간·기술스택,
domain/resume_matching.py)와 LLM 이 쓴 불릿(ai/schemas.py), `Profile`(연락처·학력·스킬태그·
언어)을 이 모양으로 조립한다.
`PdfRenderer`는 이 모양만 알면 되고 LLM 출력 스키마나 Fact 그룹핑을 몰라도 된다 — 두 어댑터가
공유하는 계약이라 contracts 에 둔다.
"""

from pydantic import BaseModel, ConfigDict, Field

from auto_apply.contracts.profile import EducationEntry, LanguageEntry


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResumeBulletView(_Frozen):
    text: str
    fact_ids: list[str] = Field(default_factory=list)


class ResumeBlockView(_Frozen):
    title: str
    period: str | None = None
    url: str | None = None
    bullets: list[ResumeBulletView] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)


class CareerEntryView(_Frozen):
    company: str
    period: str | None = None
    blocks: list[ResumeBlockView] = Field(default_factory=list)


class AssembledResume(_Frozen):
    name: str
    phone: str = ""
    email: str = ""
    summary: str
    highlights: list[ResumeBulletView] = Field(default_factory=list)
    career: list[CareerEntryView] = Field(default_factory=list)
    projects: list[ResumeBlockView] = Field(default_factory=list)
    ai_usage: list[ResumeBulletView] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    languages: list[LanguageEntry] = Field(default_factory=list)
    # 문서 내용이 아니라 승인 메타데이터다 — PdfRenderer 는 안 쓰고 승인 큐가 사람에게
    # 보여준다. ResumeDraft.content 가 승인 단계까지 흘러가는 유일한 통로라 여기 얹었다.
    caution_notes: list[str] = Field(default_factory=list)
