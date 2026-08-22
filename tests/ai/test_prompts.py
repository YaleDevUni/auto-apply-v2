"""build_resume_prompt — 불릿/블록 개수를 하드코딩하지 않고 가이드/피드백에 맡기는지 회귀 검증.

라이브 실측(2026-08-21): 예전엔 이 프롬프트가 "1~3개의 불릿을 써라"를 무조건 강제해서,
REVISE(specific)로 "3~4개로 제한해줘"를 줘도 그 지시와 매번 충돌해 4개를 낸 적이 없었다
(resume-guide 로 개수를 옮기기로 한 결정, config.py의 resume_max_* 안전 상한 코멘트 참고).
"""

from auto_apply.ai.prompts import build_resume_prompt
from auto_apply.contracts.dto import JobRef
from auto_apply.domain.resume_blocks import FactBlock

JOB = JobRef(
    job_id="j1",
    platform="wanted",
    url="https://wanted.co.kr/wd/1",
    title="백엔드 엔지니어",
    company="Acme",
    description="FastAPI 백엔드 채용",
)

BLOCK = FactBlock(
    id="acme:api",
    kind="career",
    entity="acme",
    entity_label="Acme",
    entity_period="2023.01 - 2023.12",
    entity_url=None,
    title="API 개발",
    period="2023.01 - 2023.06",
    facts=[],
)


def test_prompt_does_not_hardcode_unconditional_bullet_count() -> None:
    prompt = build_resume_prompt(JOB, [], [BLOCK])

    assert "1~3개의 불릿을 써라" not in prompt


def test_prompt_defers_block_bullet_count_to_guide_and_feedback() -> None:
    prompt = build_resume_prompt(JOB, [], [BLOCK], feedback="각 블록 불릿포인트를 3~4개로 제한해줘")

    assert "가이드]/[사용자 피드백]의 지시를 최우선으로 따르라" in prompt
    assert "각 블록 불릿포인트를 3~4개로 제한해줘" in prompt


def test_prompt_defaults_summary_to_no_motivation_statement() -> None:
    prompt = build_resume_prompt(JOB, [], [BLOCK])

    assert "지원동기·자기소개 같은 문구를 새로 지어내지 않는다" in prompt


def test_prompt_asks_summary_to_reflect_job_requested_short_intro() -> None:
    prompt = build_resume_prompt(JOB, [], [BLOCK])

    assert "간단한 자기소개/지원동기를 적어주세요" in prompt
    assert "포트폴리오 제출 요청은 이미 별도로 자동 첨부되니 summary 에서 언급하지 마라" in prompt
