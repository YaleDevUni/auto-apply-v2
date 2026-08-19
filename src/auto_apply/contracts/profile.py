"""Profile — 이력서 헤더/학력/스킬태그/언어처럼 완전히 정형화된 정보의 원천 (ARCHITECTURE.md §4).

facts.yaml 과 역할이 다르다 — Fact 는 LLM 이 문장으로 재서술할 "서술의 근거"이고, Profile 은
서술이 필요 없는 값(이름, 연락처, 학교명, GPA, 스킬 태그, 언어 등급)이라 LLM 을 거치지 않고
템플릿에 그대로 꽂는다. facts.yaml 주석에 이미 있던 판단("연락처는 grounding 대상이 아니다")을
학력/스킬/언어까지 넓힌 것.
"""

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EducationEntry(_Frozen):
    school: str
    period: str
    status: str = ""  # "졸업", "재학" 등
    degree: str = ""  # "컴퓨터공학 학사, GPA 3.9/4.5"
    note: str = ""  # "78학점 이수" 등 부기


class LanguageEntry(_Frozen):
    name: str
    level: str


class Profile(_Frozen):
    user_id: str
    name: str
    phone: str = ""
    email: str = ""
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    languages: list[LanguageEntry] = Field(default_factory=list)
