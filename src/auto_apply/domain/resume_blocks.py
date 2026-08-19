"""이력서 경력/프로젝트 블록 조립. 전부 순수 함수, LLM 미사용 (ARCHITECTURE.md §2.3).

`resume_matching.py`(fact 선별 + hallucination 탐지)와 철학은 같은 연장선이다 — 경력·프로젝트
블록의 회사명·기간·기술스택은 fact 의 구조화 필드(`entity`/`block` 계열, contracts/fact.py)에서
결정론적으로 조립하고, LLM 은 그 블록 안에서 불릿 문장만 쓴다. 파일을 나눈 이유는 "블록 조립"과
"fact 선별/grounding 검증"이 서로 다른 책임이라서다(한 파일에 있었을 땐 200줄을 넘어갔다).
"""

from dataclasses import dataclass, field

from auto_apply.contracts.fact import Fact

_BLOCK_KIND_BY_FACT_KIND = {"experience": "career", "project": "project"}


@dataclass(frozen=True, slots=True)
class FactBlock:
    """경력/프로젝트 이력서 섹션의 하위 블록 하나. entity/block 필드로 조립한 결정론적 그룹.

    `id`는 `{entity}:{block}` — LLM 구조화 출력의 `block_id`가 이 값을 그대로 인용해야 한다
    (ai/schemas.py의 BlockBullets).
    """

    id: str
    kind: str  # "career" | "project"
    entity: str
    entity_label: str
    entity_period: str | None
    title: str
    period: str | None
    facts: list[Fact] = field(default_factory=list)

    @property
    def tech_stack(self) -> list[str]:
        seen: list[str] = []
        for fact in self.facts:
            for kw in fact.keywords:
                if kw not in seen:
                    seen.append(kw)
        return seen


def group_facts_for_resume(facts: list[Fact]) -> list[FactBlock]:
    """`entity`/`block` 필드가 있는 fact 를 이력서 블록 단위로 묶는다.

    - `entity`가 없는 fact(skill/education/profile 등)는 그룹핑에서 제외한다.
    - `block`이 None 인 fact 는 블록을 만들지 않고 entity 헤더(label/period)만 제공한다
      (예: 경력의 역할 개요 fact) — 개인 프로젝트처럼 헤더 fact 가 따로 없으면 블록 자신의
      title/period 가 entity label/period 로도 쓰인다.
    - 같은 (entity, block) 를 공유하는 여러 fact 는 한 블록으로 합쳐진다(예: summary+detail).
    """
    entity_meta: dict[str, dict[str, str | None]] = {}
    block_order: list[tuple[str, str]] = []
    block_kind: dict[tuple[str, str], str] = {}
    block_label: dict[tuple[str, str], str | None] = {}
    block_period: dict[tuple[str, str], str | None] = {}
    block_facts: dict[tuple[str, str], list[Fact]] = {}

    for fact in facts:
        if not fact.entity:
            continue
        meta = entity_meta.setdefault(fact.entity, {"label": None, "period": None})
        if fact.entity_label and not meta["label"]:
            meta["label"] = fact.entity_label
        if fact.entity_period and not meta["period"]:
            meta["period"] = fact.entity_period
        if fact.block is None:
            continue
        key = (fact.entity, fact.block)
        if key not in block_facts:
            block_order.append(key)
            block_kind[key] = _BLOCK_KIND_BY_FACT_KIND.get(fact.kind, fact.kind)
            block_label[key] = None
            block_period[key] = None
            block_facts[key] = []
        if fact.block_label and not block_label[key]:
            block_label[key] = fact.block_label
        if fact.block_period and not block_period[key]:
            block_period[key] = fact.block_period
        block_facts[key].append(fact)

    blocks: list[FactBlock] = []
    for key in block_order:
        entity, _ = key
        meta = entity_meta.get(entity, {"label": None, "period": None})
        title = block_label[key] or meta["label"] or entity
        period = block_period[key] or meta["period"]
        entity_label = meta["label"] or title
        entity_period = meta["period"] or period
        blocks.append(
            FactBlock(
                id=f"{key[0]}:{key[1]}",
                kind=block_kind[key],
                entity=entity,
                entity_label=entity_label,
                entity_period=entity_period,
                title=title,
                period=period,
                facts=block_facts[key],
            )
        )
    return blocks


def select_relevant_blocks(
    blocks: list[FactBlock], job_text: str, *, max_projects: int = 3
) -> list[FactBlock]:
    """경력 블록은 전부 포함하고, 개인 프로젝트 블록만 job 관련도로 걸러 상위
    `max_projects`개로 줄인다.

    경력은 실제로 겪은 이력이라 전부 보여주는 게 정상이지만, 개인 프로젝트는 여러 개일 수
    있어 지원 공고와 무관한 것까지 다 넣으면 이력서가 산으로 간다 — `select_relevant_facts`와
    같은 겹침 점수로 우선순위를 매긴다.
    """
    haystack = job_text.casefold()

    def _score(b: FactBlock) -> int:
        return sum(1 for kw in b.tech_stack if kw and kw.casefold() in haystack)

    career = [b for b in blocks if b.kind == "career"]
    projects = [b for b in blocks if b.kind == "project"]
    ranked = sorted(projects, key=_score, reverse=True)
    selected_ids = (
        {b.id for b in ranked[:max_projects]}
        if ranked and _score(ranked[0]) > 0
        else {b.id for b in projects[:max_projects]}
    )
    return career + [b for b in projects if b.id in selected_ids]
