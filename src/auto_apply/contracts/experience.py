"""Experience — v2 Fact 모델의 일반화, 이력서·자소서 서술의 유일한 근거 (§A7, 절대 규칙 4).

v2 는 평평한 Fact 목록에 `entity`/`block` 그룹핑 키를 반복해 적었다. 여기서는 그 구조를 그대로
중첩으로 옮긴다: Experience(= v2 entity: 회사·프로젝트·활동·교육) → 선택적 하위 섹션(= v2 block)
→ fact 문장. 이력서 생성 파이프라인은 여전히 평평한 `Fact` 를 받는다 — 변환은
`domain/experience_facts.experiences_to_facts` 가 결정론적으로 한다.

`ExperienceFact.id` 는 이력서 서술이 근거로 인용하는 fact_id 다(ground_check). 한 사용자 안에서
유일해야 하므로 발급은 서비스(IdGen) 몫이고, 여기서는 한 Experience 안의 중복만 막는다.
"""

from typing import Self

from pydantic import Field, model_validator

from auto_apply.contracts._base import Frozen, IdentifierFree
from auto_apply.domain.enums import ExperienceKind


class ExperienceFact(Frozen):
    id: str
    text: str  # 이력서에 인용될 수 있는 원문 서술 (지표 포함)
    # 공고와의 매칭 신호 겸 블록 기술 태그 (v2 `keywords`). 비우면 Experience.skills 를 쓴다.
    skills: list[str] = Field(default_factory=list)


class ExperienceSection(Frozen):
    """회사 안의 하위 프로젝트처럼, 한 Experience 를 이력서에서 여러 블록으로 나눌 때 쓴다."""

    key: str  # Experience 안에서 유일 — 이력서 블록 id `{experience.id}:{key}` 의 뒷부분
    title: str
    period: str = ""
    facts: list[ExperienceFact] = Field(default_factory=list)


class Experience(IdentifierFree):
    id: str
    user_id: str
    kind: ExperienceKind
    name: str  # 회사명·프로젝트명·활동명·학교명
    role: str = ""  # 직무·역할
    period: str = ""  # 표시용 기간 ("2023.08 - 2024.04")
    # 대표 링크 (개인 프로젝트 저장소 등) — 서술이 아니라 렌더러가 링크로 그린다
    url: str | None = None
    skills: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)  # 첨부 문서 (DocumentMeta.id)
    # 섹션이 없으면 이 fact 들이 곧 블록 하나다. 섹션이 있으면 엔티티 개요(헤더 근거)다.
    facts: list[ExperienceFact] = Field(default_factory=list)
    sections: list[ExperienceSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        keys = [s.key for s in self.sections]
        if len(keys) != len(set(keys)):
            raise ValueError("sections 의 key 가 중복된다")
        ids = [f.id for f in self.all_facts()]
        if len(ids) != len(set(ids)):
            raise ValueError("fact id 가 중복된다")
        return self

    def all_facts(self) -> list[ExperienceFact]:
        return [*self.facts, *(f for s in self.sections for f in s.facts)]
