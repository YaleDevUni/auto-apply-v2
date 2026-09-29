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

    `guide`는 앞으로 모든 이력서에 적용할 규칙이다(캐시 없이 매번 새로 읽힌다). `feedback`은
    수정요청으로 이번 재생성 1회에만 반영할 지시다 — 영속 저장되지 않는다.

    블록/불릿 "개수"는 이 함수가 숫자를 강제하지 않는다 — `blocks`는 이미 domain/resume_blocks.py
    의 관대한 안전 상한(`RESUME_MAX_*`, 프롬프트 크기 폭주 방지용일 뿐)만 거친 상태라 사실상
    전부 넘어온다. 몇 개를 실제로 쓸지는 LLM이 [이력서 작성 가이드]/[사용자 피드백]의 지시를
    보고 정한다 — 예전엔 이 프롬프트에 "1~3개" 를 하드코딩해뒀는데, 수정요청으로 사용자가
    "3~4개로 제한해줘" 라고 줘도 매번 이 하드코딩과 충돌해 4개를 낸 적이 없었다(라이브 실측,
    2026-08-21). 개수 지시가 아예 없을 때만 쓰는 fallback 문구로 남겨뒀다.
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
        "만들거나 제목·기간을 다시 쓰지 마라(그건 이미 결정되어 있다). [블록 목록]에 있는 "
        "블록·불릿을 몇 개나 실제로 쓸지는 아래 [이력서 작성 가이드]/[사용자 피드백]의 지시를 "
        "최우선으로 따르라 — 그런 지시가 없을 때만 이 공고와의 관련성을 스스로 판단해 회사/"
        "프로젝트당 상위 몇 개, 블록마다 1~3개 정도로 적당히 골라 써라. 근거가 부족하거나 "
        "관련성이 낮아 제외하는 블록은 blocks 출력에서 빼거나 bullets 를 빈 리스트로 둬도 된다.\n"
        "highlights 는 전체를 아우르는 상단 요약 불릿 3~5개, ai_usage 는 AI/LLM 활용 경험을 "
        "요약하는 짧은 불릿 0~2개다.\n"
        "summary 는 기본적으로 지원동기·자기소개 같은 문구를 새로 지어내지 않는다 — 이름/직무를 "
        "요약하는 한 줄이면 충분하다. 다만 [공고 설명]에 '간단한 자기소개/지원동기를 적어주세요' "
        "처럼 이 한 줄 요약에 담을 수 있는 요청이 있으면 그 요청에 맞춰 summary 에 반영하라.\n"
        "caution_notes 에는 사람이 승인 버튼을 누르기 전에 알아야 할 주관적인 주의사항을 "
        "짧은 문장으로 적어라(예: '경력 요건 대비 근거가 빠듯함', '마감이 임박해 검토 시간이 "
        "짧음') — 근거 fact_id 가 필요 없는 자유 서술이다. 특별히 알릴 게 없으면 빈 리스트로 "
        "둬라.\n\n"
        f"[공고 설명]\n{job.description or '(설명 없음)'}\n\n"
        f"[블록 목록]\n{blocks_text}\n\n"
        f"[사실 목록 — highlights/ai_usage 용]\n{fact_lines}"
    )
    if guide:
        prompt += f"\n\n[이력서 작성 가이드 — 항상 지켜라]\n{guide}"
    if feedback:
        prompt += f"\n\n[이번 재생성에 대한 사용자 피드백 — 반드시 반영하라]\n{feedback}"
    return prompt


def reprompt_error_suffix(error: str) -> str:
    """스키마 위반 재시도용 추가 지시문 — 원본 프롬프트는 포함하지 않는다.

    호출부(`adapters/resume/simple.py`)가 원본 프롬프트를 `cache_prefix`로, 이 접미어를
    `prompt`로 나눠 보낸다 — 재시도 때마다 원본이 캐시로 읽히고 이 짧은 문자열만 새로
    처리된다([[claude-cli-prompt-cache-redesign]]).
    """
    return f"\n\n[이전 시도 오류] 스키마를 위반했다: {error}\n정확한 JSON 스키마로 다시 답하라."
