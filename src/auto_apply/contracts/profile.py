"""Profile — 인적사항. 서술이 필요 없는 정형 값이라 LLM 을 거치지 않고 그대로 쓴다 (§A7).

Experience(contracts/experience.py)와 역할이 다르다 — Experience 의 fact 는 LLM 이 문장으로
재서술할 "서술의 근거"이고, Profile 은 이력서 헤더·지원서 인적사항 칸에 그대로 꽂힌다.

필드는 언어 중립이다(D14): 키는 영문 식별자, 열거값은 `domain/enums.py`, 화면 라벨은 웹 i18n.
**추가 정보**(`additional`)는 전부 선택이다 — 비어 있으면 실행 중 `ask_user` 로 묻고 답을
답변KB 에 남긴다(D10). 그래서 None("아직 모름")과 False/빈 값("해당 없음")을 구분한다.
"""

from pydantic import Field

from auto_apply.contracts._base import Frozen, IdentifierFree
from auto_apply.domain.enums import MilitaryStatus


class EducationEntry(Frozen):
    school: str
    period: str
    status: str = ""  # "졸업", "재학" 등
    degree: str = ""  # "컴퓨터공학 학사, GPA 3.9/4.5"
    note: str = ""  # "78학점 이수" 등 부기


class LanguageEntry(Frozen):
    name: str
    level: str


class ProfileLink(Frozen):
    label: str  # "GitHub", "블로그" — 사용자가 붙인 이름 그대로
    url: str


class MilitaryService(Frozen):
    status: MilitaryStatus
    branch: str = ""  # 군별
    rank: str = ""  # 계급
    period: str = ""
    note: str = ""  # 면제 사유 등


class AdditionalInfo(Frozen):
    """지원서마다 묻는 선택 항목. None = 아직 입력 안 함 → 실행 중 질문 (D10)."""

    military: MilitaryService | None = None
    veteran: bool | None = None  # 보훈 대상 여부
    disability: bool | None = None  # 장애 여부
    # "4,000만원", "회사 내규에 따름" — 사이트마다 양식이 달라 자유 텍스트로 둔다
    desired_salary: str | None = None
    available_from: str | None = None  # 입사 가능일 — "즉시", "2026-11-01" 등
    residence: str | None = None  # 거주 지역


class Profile(IdentifierFree):
    user_id: str
    # 기본
    name: str
    phone: str = ""
    email: str = ""
    links: list[ProfileLink] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    languages: list[LanguageEntry] = Field(default_factory=list)
    # 추가 정보 (전부 선택)
    additional: AdditionalInfo = Field(default_factory=AdditionalInfo)
