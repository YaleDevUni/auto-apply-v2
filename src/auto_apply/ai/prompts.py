"""이력서 생성 프롬프트. 순수 문자열 조립 — LLM 호출은 adapters/resume 쪽에서 한다."""

from auto_apply.contracts.dto import JobRef
from auto_apply.contracts.fact import Fact


def build_resume_prompt(job: JobRef, facts: list[Fact]) -> str:
    fact_lines = "\n".join(f"- ({f.id}) {f.content}" for f in facts) or "(등록된 사실 없음)"
    return (
        f"{job.company} / {job.title} 공고에 지원할 이력서 초안을 작성하라.\n"
        "아래 [사실 목록]에 없는 경력·수치·프로젝트는 절대 지어내지 마라. "
        "각 highlight 는 근거로 쓴 fact_id 를 fact_ids 에 반드시 포함해야 한다 — "
        "근거 없는 서술이나 목록에 없는 fact_id 인용은 반려된다.\n\n"
        f"[공고 설명]\n{job.description or '(설명 없음)'}\n\n"
        f"[사실 목록]\n{fact_lines}"
    )


def reprompt_with_error(prompt: str, error: str) -> str:
    return (
        f"{prompt}\n\n[이전 시도 오류] 스키마를 위반했다: {error}\n"
        "정확한 JSON 스키마로 다시 답하라."
    )
