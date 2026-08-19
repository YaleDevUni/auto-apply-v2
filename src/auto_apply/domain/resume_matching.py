"""이력서 생성의 fact 선별 + hallucination 탐지. 전부 순수 함수, LLM 미사용 (ARCHITECTURE.md §2.3).

Recipe 의 `value_ref`(참조만, 리터럴 금지, §3)와 같은 철학 — 이력서의 모든 서술도 fact_id 로
근거를 참조해야 한다. `ground_check`가 review 게이트의 첫 체크(hallucinated claim 탐지)다.
"""

from auto_apply.contracts.dto import ResumeDraft
from auto_apply.contracts.fact import Fact


def select_relevant_facts(facts: list[Fact], job_text: str, *, limit: int = 8) -> list[Fact]:
    """job_text(제목+설명)와 키워드가 겹치는 fact 를 점수순으로 뽑는다.

    아무 것도 안 겹치면(설명이 비어있거나 fact 에 keywords 가 없는 등) 필터링 없이 전부
    반환한다 — 잘못 걸러서 정말 필요한 fact 가 LLM 에게 아예 안 보이는 쪽이 더 나쁘다.
    """
    haystack = job_text.casefold()

    def _score(fact: Fact) -> int:
        return sum(1 for kw in fact.keywords if kw and kw.casefold() in haystack)

    scored = sorted(facts, key=_score, reverse=True)
    if scored and _score(scored[0]) > 0:
        ranked = [f for f in scored if _score(f) > 0]
    else:
        ranked = list(facts)
    return ranked[:limit]


def ground_check(draft: ResumeDraft, facts: list[Fact]) -> list[str]:
    """draft 의 모든 서술이 실제 fact 로 근거되는지 확인한다.

    - highlight 에 fact_id 가 하나도 없으면: 근거 없는 서술 (hallucination 위험).
    - 존재하지 않는 fact_id 를 인용하면: 지어낸 근거 (명백한 hallucination).
    """
    known_ids = {f.id for f in facts}
    issues: list[str] = []

    highlights = draft.content.get("highlights", [])
    if not isinstance(highlights, list):
        highlights = []
    for item in highlights:
        if not isinstance(item, dict):
            continue
        text = item.get("text", "")
        fact_ids = item.get("fact_ids") or []
        if not fact_ids:
            issues.append(f"근거 fact 없는 서술: {text!r}")
            continue
        for fid in fact_ids:
            if fid not in known_ids:
                issues.append(f"존재하지 않는 fact_id 참조: {fid!r} ({text!r})")

    for fid in draft.used_fact_ids:
        if fid not in known_ids:
            issues.append(f"존재하지 않는 fact_id 참조: {fid!r}")

    return issues
