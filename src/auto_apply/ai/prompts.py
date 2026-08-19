"""이력서 생성 프롬프트. 순수 문자열 조립 — LLM 호출은 adapters/resume 쪽에서 한다."""

from auto_apply.contracts.dto import JobRef
from auto_apply.contracts.fact import Fact
from auto_apply.domain.resume_blocks import FactBlock


def build_resume_prompt(
    job: JobRef,
    facts: list[Fact],
    blocks: list[FactBlock],
    *,
    guide: str = "",
    feedback: str = "",
) -> str:
    """`facts`는 top-level highlights/ai_usage 근거 풀, `blocks`는 경력/프로젝트 하위 블록이다.

    블록의 제목·기간·회사명은 이미 결정론 코드(domain/resume_matching.group_facts_for_resume)가
    정했다 — LLM 은 block_id 를 그대로 인용하고 그 블록에 속한 fact 를 근거로 불릿만 쓴다.

    `guide`는 REVISE(general)로 사람이 승인한, 앞으로 모든 이력서에 적용할 규칙이다
    (config/resume_guide.md, 캐시 없이 매번 새로 읽힌다). `feedback`은 REVISE(specific)로
    이번 재생성 1회에만 반영할 지시다 — 영속 저장되지 않는다.
    """
    fact_lines = "\n".join(f"- ({f.id}) {f.content}" for f in facts) or "(등록된 사실 없음)"

    block_sections = []
    for b in blocks:
        block_facts = "\n".join(f"  - ({f.id}) {f.content}" for f in b.facts) or "  (사실 없음)"
        block_sections.append(f"[블록 {b.id}] {b.title} ({b.period or '기간 미상'})\n{block_facts}")
    blocks_text = "\n\n".join(block_sections) or "(블록 없음)"

    prompt = (
        f"{job.company} / {job.title} 공고에 지원할 이력서 초안을 작성하라.\n"
        "아래 [블록 목록]과 [사실 목록]에 없는 경력·수치·프로젝트는 절대 지어내지 마라. "
        "highlights/ai_usage 의 각 항목과 blocks[].bullets 의 각 항목은 근거로 쓴 fact_id 를 "
        "fact_ids 에 반드시 포함해야 한다 — 근거 없는 서술이나 목록에 없는 fact_id "
        "인용은 반려된다.\n"
        "blocks 출력에서는 [블록 목록]에 있는 block_id 를 정확히 그대로 써라 — 새 블록을 "
        "만들거나 제목·기간을 다시 쓰지 마라(그건 이미 결정되어 있다). 각 블록마다 그 블록에 "
        "속한 사실만 근거로 1~3개의 불릿을 써라. 근거가 하나도 없는 블록은 건너뛰어도 된다.\n"
        "highlights 는 전체를 아우르는 상단 요약 불릿 3~5개, ai_usage 는 AI/LLM 활용 경험을 "
        "요약하는 짧은 불릿 0~2개다.\n\n"
        f"[공고 설명]\n{job.description or '(설명 없음)'}\n\n"
        f"[블록 목록]\n{blocks_text}\n\n"
        f"[사실 목록 — highlights/ai_usage 용]\n{fact_lines}"
    )
    if guide:
        prompt += f"\n\n[이력서 작성 가이드 — 항상 지켜라]\n{guide}"
    if feedback:
        prompt += f"\n\n[이번 재생성에 대한 사용자 피드백 — 반드시 반영하라]\n{feedback}"
    return prompt


def build_guide_patch_prompt(guide: str, feedback: str, job: JobRef) -> str:
    """`feedback`(REVISE/general)을 가이드에 반영할 `{old, new}` 치환 쌍을 제안하게 한다.

    전문을 다시 쓰게 하지 않는다 — 지시하지 않은 다른 규칙이 조용히 사라질 수 있다
    (domain/guide_patch.py 참고). `old`는 [현재 가이드]에서 정확히 그대로 인용해야 한다.
    """
    return (
        "아래 [현재 가이드]는 이력서를 쓸 때마다 항상 지켜야 하는 규칙이다. [사용자 피드백]을 "
        "반영해 이 가이드를 고치는 최소한의 치환 쌍(old, new)을 제안하라.\n"
        "old 는 [현재 가이드]에 있는 문자열을 한 글자도 틀리지 않고 그대로 인용해야 한다 — "
        "그래야 정확히 그 자리만 바뀐다. 전체를 다시 쓰지 마라. 가이드가 비어 있으면 old 를 "
        "빈 문자열로 두고 new 에 새 규칙 전체를 써라.\n"
        f"[공고 맥락 — 이 피드백이 나온 상황]\n{job.company} / {job.title}\n\n"
        f"[현재 가이드]\n{guide or '(비어 있음)'}\n\n"
        f"[사용자 피드백]\n{feedback}"
    )


def reprompt_with_error(prompt: str, error: str) -> str:
    return (
        f"{prompt}\n\n[이전 시도 오류] 스키마를 위반했다: {error}\n"
        "정확한 JSON 스키마로 다시 답하라."
    )
