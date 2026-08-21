"""조립된 이력서 최종 콘텐츠 — `ResumeDraft.content`에 담기는 실제 모양(ARCHITECTURE.md §2.3).

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
    # 문서 내용이 아니라 실행 메타데이터다 — PdfRenderer는 이 필드를 안 쓴다. Recipe 실행
    # 단계(ExecutionContext.profile)까지 흘러가는 유일한 통로가 ResumeDraft.content 라서
    # 여기 얹었다(domain/portfolio 같은 새 계약을 따로 만들 만큼 크지 않다).
    portfolio_filename: str = ""
    # 문서 내용이 아니라 승인 메타데이터다(§ wanted-application-caution-indicators-backlog) —
    # PdfRenderer 는 안 쓰고 workflows/application.py 가 DecisionRequest.caution_notes 로
    # 그대로 옮긴다. portfolio_filename 과 같은 이유로 여기 얹었다.
    caution_notes: list[str] = Field(default_factory=list)
