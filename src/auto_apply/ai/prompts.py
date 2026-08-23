"""이력서 생성 프롬프트. 순수 문자열 조립 — LLM 호출은 adapters/resume 쪽에서 한다."""

from auto_apply.contracts.dto import JobRef
from auto_apply.contracts.fact import Fact
from auto_apply.contracts.recipe import AutomationRecipe
from auto_apply.domain.resume_blocks import FactBlock

# DOM 스냅샷은 실제 페이지 전체 HTML이라 프롬프트 토큰을 순식간에 태운다 — 이 길이로 잘라도
# selector 후보를 찾기엔 보통 충분하다(폼은 대개 문서 앞쪽 절반 안에 있다, 실측).
_SNAPSHOT_CHAR_LIMIT = 16_000


def build_resume_prompt(
    job: JobRef,
    facts: list[Fact],
    blocks: list[FactBlock],
    *,
    guide: str = "",
    feedback: str = "",
    portfolio_categories: list[str] | None = None,
) -> str:
    """`facts`는 top-level highlights/ai_usage 근거 풀, `blocks`는 경력/프로젝트 하위 블록이다.

    블록의 제목·기간·회사명은 이미 결정론 코드(domain/resume_matching.group_facts_for_resume)가
    정했다 — LLM 은 block_id 를 그대로 인용하고 그 블록에 속한 fact 를 근거로 불릿만 쓴다.

    `guide`는 REVISE(general)로 사람이 승인한, 앞으로 모든 이력서에 적용할 규칙이다
    (config/resume_guide.{platform}.md, 캐시 없이 매번 새로 읽힌다). `feedback`은 REVISE(specific)로
    이번 재생성 1회에만 반영할 지시다 — 영속 저장되지 않는다.

    블록/불릿 "개수"는 이 함수가 숫자를 강제하지 않는다 — `blocks`는 이미 domain/resume_blocks.py
    의 관대한 안전 상한(`RESUME_MAX_*`, 프롬프트 크기 폭주 방지용일 뿐)만 거친 상태라 사실상
    전부 넘어온다. 몇 개를 실제로 쓸지는 LLM이 [이력서 작성 가이드]/[사용자 피드백]의 지시를
    보고 정한다 — 예전엔 이 프롬프트에 "1~3개" 를 하드코딩해뒀는데, REVISE(specific)로 사용자가
    "3~4개로 제한해줘" 라고 줘도 매번 이 하드코딩과 충돌해 4개를 낸 적이 없었다(라이브 실측,
    2026-08-21). 개수 지시가 아예 없을 때만 쓰는 fallback 문구로 남겨뒀다.

    `portfolio_categories`는 `config/portfolio_map.yaml`의 카테고리 라벨 목록이다 — LLM은 이
    목록 중 하나를 `job_category`로 그대로 인용하거나(새 라벨 창작 금지), 안 맞으면 비워둔다.
    실제 첨부파일명으로 바꾸는 매핑은 코드가 한다(adapters/resume/_assemble.py).
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
        "처럼 이 한 줄 요약에 담을 수 있는 요청이 있으면 그 요청에 맞춰 summary 에 반영하라. "
        "포트폴리오 제출 요청은 이미 별도로 자동 첨부되니 summary 에서 언급하지 마라.\n"
        "caution_notes 에는 사람이 승인 버튼을 누르기 전에 알아야 할 주관적인 주의사항을 "
        "짧은 문장으로 적어라(예: '경력 요건 대비 근거가 빠듯함', '마감이 임박해 검토 시간이 "
        "짧음') — 근거 fact_id 가 필요 없는 자유 서술이다. 첨부서류·포트폴리오 안내는 이미 "
        "별도로 처리되니 여기서 다시 언급하지 마라. 특별히 알릴 게 없으면 빈 리스트로 둬라.\n\n"
        f"[공고 설명]\n{job.description or '(설명 없음)'}\n\n"
        f"[블록 목록]\n{blocks_text}\n\n"
        f"[사실 목록 — highlights/ai_usage 용]\n{fact_lines}"
    )
    if guide:
        prompt += f"\n\n[이력서 작성 가이드 — 항상 지켜라]\n{guide}"
    if feedback:
        prompt += f"\n\n[이번 재생성에 대한 사용자 피드백 — 반드시 반영하라]\n{feedback}"
    if portfolio_categories:
        categories_text = ", ".join(portfolio_categories)
        prompt += (
            f"\n\n[직무 카테고리]\n다음 중 이 공고와 가장 잘 맞는 카테고리를 job_category 에 "
            f"그대로 인용하라(새 라벨을 만들지 마라): {categories_text}\n"
            "확신이 서지 않으면 job_category 를 빈 문자열로 둬라."
        )
    return prompt


def build_guide_patch_prompt(guide: str, feedback: str, job: JobRef) -> str:
    """`feedback`(REVISE/general)을 가이드에 반영할 `{old, new}` 치환 쌍 목록을 제안하게 한다.

    전문을 다시 쓰게 하지 않는다 — 지시하지 않은 다른 규칙이 조용히 사라질 수 있다
    (domain/guide_patch.py 참고). `old`는 [현재 가이드]에서 정확히 그대로 인용해야 한다.
    [사용자 피드백]에 서로 다른 지시가 여러 개 섞여 있을 수 있어 patches 를 여러 개 낼 수
    있게 한다 — "최소한의 치환 쌍 하나"만 요구했더니 다지시 피드백 중 일부가 조용히
    누락되는 걸 라이브 테스트로 실측했다(메모리 resume-revise-feedback-design).
    """
    return (
        "아래 [현재 가이드]는 이력서를 쓸 때마다 항상 지켜야 하는 규칙이다. [사용자 피드백]을 "
        "반영해 이 가이드를 고치는 치환 쌍(old, new) 목록을 제안하라.\n"
        "[사용자 피드백]에 서로 구분되는 지시가 여러 개 있으면 지시마다 별도의 patch 항목을 "
        "하나씩 내라 — 하나로 묶어도 뜻이 그대로면 묶어도 되지만, 일부 지시만 반영하고 나머지를 "
        "건너뛰면 안 된다. [사용자 피드백]의 모든 지시가 patches 어딘가에 반영되어야 한다.\n"
        "각 patch 의 old 는 [현재 가이드]에 있는 문자열을 한 글자도 틀리지 않고 그대로 인용해야 "
        "한다 — 그래야 정확히 그 자리만 바뀐다. 전체를 다시 쓰지 마라. 서로 다른 patch 의 old "
        "가 겹치는 구간을 가리키게 하지 마라(순서대로 적용되며, 겹치면 뒤 patch 가 찾는 문자열이 "
        "이미 바뀐 뒤라 실패한다). 가이드가 비어 있거나 완전히 새 규칙을 추가하는 patch 는 old "
        "를 빈 문자열로 두고 new 에 새 규칙 전체를 써라.\n"
        f"[공고 맥락 — 이 피드백이 나온 상황]\n{job.company} / {job.title}\n\n"
        f"[현재 가이드]\n{guide or '(비어 있음)'}\n\n"
        f"[사용자 피드백]\n{feedback}"
    )


def build_recipe_diff_prompt(
    previous: AutomationRecipe, snapshot_html: str, failure_detail: str
) -> str:
    """recipe 수선 제안 프롬프트 (§2.4 node B).

    `previous`의 actions 를 JSON 으로 그대로 보여줘 "무엇을 고치는지"를 diff 관점으로 이해하게
    한다 — 전체를 새로 설계하지 말고 실패한 지점만 고치라고 명시한다(Recipe/가이드 patch와 같은
    "최소 변경" 철학). `expected_elements`/`validation_rules`는 코드가 그대로 들고 가므로
    (domain/recipe_repair.py) 프롬프트에 안 보여준다 — LLM이 건드릴 필드가 아니다.

    `failure_detail`은 실제 실패 사유 문자열(activity_failure()의 reason)이어야 한다 — 예전엔
    `propose_recipe_diff`가 이 자리에 실수로 form_hash 를 넣고 있어서 LLM이 왜 실패했는지
    전혀 모른 채 diff 를 냈다(메모리 wanted-goto-timeout-misdiagnosed-as-recipe-bug). "timeout
    이면 selector 를 건드리지 마라" 류의 지시는 일부러 안 넣는다 — Playwright 는 페이지 로드가
    느려 나는 timeout 과 selector 가 아예 틀려 대상이 안 나타나 나는 timeout 을 똑같은 문구
    ("Timeout Nms exceeded")로 낸다. 이 둘을 프롬프트 지시로 뭉뚱그리면 후자(진짜 셀렉터 문제)
    를 못 고치게 막을 위험이 있고, LLM 이 그 지시를 얼마나 충실히 따를지도 보장이 없다 — 실제
    실패 사유와 DOM 을 그대로 보여주고 판단은 LLM 에게 맡긴다.
    """
    actions_json = previous.model_dump_json(include={"actions", "success_signals"}, indent=2)
    snapshot = snapshot_html[:_SNAPSHOT_CHAR_LIMIT]
    truncated_note = (
        "\n(스냅샷이 길어 앞부분만 잘랐다)" if len(snapshot_html) > _SNAPSHOT_CHAR_LIMIT else ""
    )
    return (
        f"{previous.platform} 지원 폼에서 아래 [실패한 recipe]의 actions 를 실행하다 "
        f"[실패 사유]로 실패했다. [현재 페이지 DOM]을 보고 실패한 지점의 selector 를 실제 DOM과 "
        "맞게 고쳐서 actions 전체(성공한 앞부분 포함)를 다시 내라 — 일부만 내면 나머지 단계가 "
        "빠진 recipe 가 된다. 실패하지 않은 부분은 원본 selector 를 그대로 유지해라(불필요한 "
        "변경은 다음 실행에서 또 다른 회귀를 만들 수 있다).\n"
        "value_ref 는 'profile.xxx'/'upload.xxx' 형태의 참조만 허용된다 — 실제 값(이메일 "
        "주소 등)을 리터럴로 쓰지 마라. success_signals 는 제출 완료를 판정하는 텍스트 목록이다 "
        "— DOM에서 실제로 보이는 문구가 있으면 그걸 반영하고, 없으면 원본 값을 유지해라.\n\n"
        f"[실패 사유]\n{failure_detail}\n\n"
        f"[실패한 recipe]\n{actions_json}\n\n"
        f"[현재 페이지 DOM]\n{snapshot}{truncated_note}"
    )


def reprompt_error_suffix(error: str) -> str:
    """스키마 위반 재시도용 추가 지시문 — 원본 프롬프트는 포함하지 않는다.

    호출부(`adapters/resume/simple.py`)가 원본 프롬프트를 `cache_prefix`로, 이 접미어를
    `prompt`로 나눠 보낸다 — 재시도 때마다 원본이 캐시로 읽히고 이 짧은 문자열만 새로
    처리된다([[claude-cli-prompt-cache-redesign]]).
    """
    return f"\n\n[이전 시도 오류] 스키마를 위반했다: {error}\n정확한 JSON 스키마로 다시 답하라."
