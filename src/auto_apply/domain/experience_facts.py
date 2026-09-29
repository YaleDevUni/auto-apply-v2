"""Experience(중첩, 저장 모델) → Fact(평평, 이력서 파이프라인 입력) 변환. 순수 함수 (§A7).

이력서 블록 조립(`resume_blocks.group_facts_for_resume`)과 grounding(`resume_matching`)은 v2 의
평평한 Fact 계약 그대로다. 저장 모델만 중첩으로 바꾸고 그 사이를 여기서 결정론적으로 잇는다.

- company → "experience"(경력 블록), project → "project"(프로젝트 블록). entity = experience.id.
- 섹션이 있으면 섹션 하나 = 블록 하나, Experience 자신의 facts 는 엔티티 헤더 근거(block 없음).
- 섹션이 없으면 Experience 자신의 facts 가 블록 `main` 하나가 된다(v2 개인 프로젝트 관례).
- fact 의 skills 가 비면 Experience.skills 를 매칭 키워드로 물려받는다.
- activity·education 은 블록을 만들지 않는다 — 이력서 템플릿에 해당 섹션이 아직 없어서다.
  fact 로는 그대로 넘겨 선별·근거 인용에는 쓰인다.
"""

from auto_apply.contracts.experience import Experience, ExperienceFact
from auto_apply.contracts.fact import Fact
from auto_apply.domain.enums import ExperienceKind

_FACT_KIND = {
    ExperienceKind.COMPANY: "experience",
    ExperienceKind.PROJECT: "project",
    ExperienceKind.ACTIVITY: "activity",
    ExperienceKind.EDUCATION: "education",
}
_BLOCK_KINDS = frozenset({ExperienceKind.COMPANY, ExperienceKind.PROJECT})
MAIN_BLOCK = "main"


def _label(exp: Experience) -> str:
    return f"{exp.name}({exp.role})" if exp.role else exp.name


def _fact(
    exp: Experience,
    fact: ExperienceFact,
    *,
    block: str | None = None,
    block_label: str | None = None,
    block_period: str | None = None,
) -> Fact:
    grouped = exp.kind in _BLOCK_KINDS
    return Fact(
        id=fact.id,
        user_id=exp.user_id,
        kind=_FACT_KIND[exp.kind],
        content=fact.text,
        keywords=list(fact.skills or exp.skills),
        source=f"experience:{exp.id}",
        entity=exp.id if grouped else None,
        entity_label=_label(exp) if grouped else None,
        entity_period=(exp.period or None) if grouped else None,
        entity_url=exp.url if grouped else None,
        block=block if grouped else None,
        block_label=block_label if grouped else None,
        block_period=block_period if grouped else None,
    )


def experience_to_facts(exp: Experience) -> list[Fact]:
    if not exp.sections:
        # 경력 블록 제목이 회사 헤더와 똑같이 찍히지 않게 역할을 쓴다.
        title = (exp.role or exp.name) if exp.kind is ExperienceKind.COMPANY else exp.name
        return [
            _fact(exp, f, block=MAIN_BLOCK, block_label=title, block_period=exp.period or None)
            for f in exp.facts
        ]
    facts = [_fact(exp, f) for f in exp.facts]
    for section in exp.sections:
        facts.extend(
            _fact(
                exp,
                f,
                block=section.key,
                block_label=section.title,
                block_period=section.period or None,
            )
            for f in section.facts
        )
    return facts


def experiences_to_facts(experiences: list[Experience]) -> list[Fact]:
    """순서를 보존한다 — 이력서의 회사·프로젝트 등장 순서가 저장 순서를 따른다."""
    return [fact for exp in experiences for fact in experience_to_facts(exp)]
