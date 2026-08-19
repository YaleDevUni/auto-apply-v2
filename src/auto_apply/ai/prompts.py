"""이력서 생성 프롬프트. 순수 문자열 조립 — LLM 호출은 adapters/resume 쪽에서 한다."""

from auto_apply.contracts.dto import JobRef
from auto_apply.contracts.fact import Fact
from auto_apply.domain.resume_blocks import FactBlock


def build_resume_prompt(job: JobRef, facts: list[Fact], blocks: list[FactBlock]) -> str:
    """`facts`는 top-level highlights/ai_usage 근거 풀, `blocks`는 경력/프로젝트 하위 블록이다.

    블록의 제목·기간·회사명은 이미 결정론 코드(domain/resume_matching.group_facts_for_resume)가
    정했다 — LLM 은 block_id 를 그대로 인용하고 그 블록에 속한 fact 를 근거로 불릿만 쓴다.
    """
    fact_lines = "\n".join(f"- ({f.id}) {f.content}" for f in facts) or "(등록된 사실 없음)"

    block_sections = []
    for b in blocks:
        block_facts = "\n".join(f"  - ({f.id}) {f.content}" for f in b.facts) or "  (사실 없음)"
        block_sections.append(f"[블록 {b.id}] {b.title} ({b.period or '기간 미상'})\n{block_facts}")
    blocks_text = "\n\n".join(block_sections) or "(블록 없음)"

    return (
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


def reprompt_with_error(prompt: str, error: str) -> str:
    return (
        f"{prompt}\n\n[이전 시도 오류] 스키마를 위반했다: {error}\n"
        "정확한 JSON 스키마로 다시 답하라."
    )
