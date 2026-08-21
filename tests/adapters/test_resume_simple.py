"""SimpleResumeGenerator/SimpleResumeReviewer — fact 기반 생성 + review 게이트 (§2.3)."""

import pytest

from auto_apply.adapters.clock.system import UuidIdGen
from auto_apply.adapters.facts.static import StaticFactSource
from auto_apply.adapters.guide.static import StaticGuideSource
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.portfolio.static import StaticPortfolioSource
from auto_apply.adapters.profile.static import StaticProfileSource
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.contracts.dto import GenerateResumeRequest, JobRef, ResumeDraft, ReviewRequest
from auto_apply.contracts.fact import Fact
from auto_apply.contracts.portfolio import PortfolioMap
from auto_apply.contracts.profile import Profile
from auto_apply.domain.errors import LLMSchemaViolation
from auto_apply.ports.guide import GuideSource

JOB = JobRef(
    job_id="j1",
    platform="fixture",
    url="https://fixture.local/1",
    title="백엔드 엔지니어",
    company="Acme",
    description="FastAPI 경험 우대",
)
FACT = Fact(
    id="exp-1", user_id="u1", kind="experience", content="FastAPI 3년", keywords=["FastAPI"]
)
FACT_BLOCK = Fact(
    id="exp-block-1",
    user_id="u1",
    kind="experience",
    content="Acme 에서 결제 API 를 설계했다.",
    keywords=["FastAPI", "결제"],
    entity="acme",
    entity_label="Acme(백엔드 인턴)",
    entity_period="2023.01 - 2023.12",
    block="payment-api",
    block_label="결제 API 개발",
    block_period="2023.01 - 2023.06",
)
FACT_BLOCK_DEVOPS = Fact(
    id="exp-block-2",
    user_id="u1",
    kind="experience",
    content="Acme 에서 배포 파이프라인을 구축했다.",
    keywords=["Docker", "CI/CD"],
    entity="acme",
    entity_label="Acme(백엔드 인턴)",
    entity_period="2023.01 - 2023.12",
    block="devops",
    block_label="배포 자동화",
    block_period="2023.07 - 2023.12",
)
PROFILE = Profile(user_id="u1", name="테스터")
VALID_PAYLOAD = {
    "summary": "FastAPI 경험을 살린 백엔드 엔지니어입니다",
    "highlights": [{"text": "FastAPI 결제 API 개발", "fact_ids": ["exp-1"]}],
}


def _profile_source() -> StaticProfileSource:
    return StaticProfileSource([PROFILE])


def _portfolio_source(categories: dict[str, str] | None = None) -> StaticPortfolioSource:
    return StaticPortfolioSource([PortfolioMap(user_id="u1", categories=categories or {})])


class _RecordingLLM(StubLLM):
    """StubLLM 을 감싸 마지막 structured() 호출의 실제 전송 내용을 기록한다 — guide/feedback 이

    실제로 프롬프트에 실리는지(SimpleResumeGenerator 배선) 검증하는 데 쓴다. `cache_prefix +
    prompt`가 모델에 보내지는 전체 내용이라 그 결합을 기록한다.
    """

    def __init__(self, payloads: list[dict[str, object]]) -> None:
        super().__init__(payloads=payloads)
        self.last_prompt: str = ""

    async def structured(  # type: ignore[override]
        self, prompt, schema, *, max_tokens=2048, cache_prefix=""
    ):
        self.last_prompt = cache_prefix + prompt
        return await super().structured(
            prompt, schema, max_tokens=max_tokens, cache_prefix=cache_prefix
        )


async def test_generate_grounds_used_fact_ids_from_llm_output():
    facts = StaticFactSource([FACT])
    gen = SimpleResumeGenerator(
        StubLLM(payloads=[VALID_PAYLOAD]),
        UuidIdGen(),
        facts,
        _profile_source(),
        _portfolio_source(),
        StaticGuideSource(),
    )
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    assert draft.used_fact_ids == ["exp-1"]
    assert draft.content["summary"] == VALID_PAYLOAD["summary"]
    assert draft.content["name"] == "테스터"


async def test_generate_passes_guide_and_feedback_into_the_prompt():
    """가이드(영속, general REVISE)와 feedback(1회성, specific REVISE)이 모두 프롬프트에 실린다."""
    facts = StaticFactSource([FACT])
    llm = _RecordingLLM([VALID_PAYLOAD])
    guide: GuideSource = StaticGuideSource("항상 존댓말로 쓴다")
    gen = SimpleResumeGenerator(
        llm, UuidIdGen(), facts, _profile_source(), _portfolio_source(), guide
    )
    await gen.generate(
        GenerateResumeRequest(
            application_id="a1", user_id="u1", job=JOB, feedback="자기소개를 더 짧게"
        )
    )
    assert "항상 존댓말로 쓴다" in llm.last_prompt
    assert "자기소개를 더 짧게" in llm.last_prompt


async def test_generate_passes_portfolio_categories_into_the_prompt():
    """LLM 이 새 라벨을 창작하지 않도록, 실제 매핑에 있는 카테고리 라벨만 프롬프트에 실린다."""
    facts = StaticFactSource([FACT])
    llm = _RecordingLLM([VALID_PAYLOAD])
    portfolio = _portfolio_source(
        {"개발자": "포트폴리오_풀스택.pdf", "데브옵스": "포트폴리오_데브옵스.pdf"}
    )
    gen = SimpleResumeGenerator(
        llm, UuidIdGen(), facts, _profile_source(), portfolio, StaticGuideSource()
    )
    await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    assert "개발자" in llm.last_prompt
    assert "데브옵스" in llm.last_prompt


async def test_generate_maps_job_category_to_portfolio_filename():
    """LLM 이 고른 job_category 라벨을 코드가 실제 첨부파일명으로 바꾼다 — 파일명 자체는

    LLM 이 만들지 않는다("AI는 생성만, 판정·조합은 코드")."""
    facts = StaticFactSource([FACT])
    payload = {**VALID_PAYLOAD, "job_category": "개발자"}
    portfolio = _portfolio_source({"개발자": "박예일_포트폴리오_풀스택.pdf"})
    gen = SimpleResumeGenerator(
        StubLLM(payloads=[payload]),
        UuidIdGen(),
        facts,
        _profile_source(),
        portfolio,
        StaticGuideSource(),
    )
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    assert draft.content["portfolio_filename"] == "박예일_포트폴리오_풀스택.pdf"


async def test_generate_leaves_portfolio_filename_empty_when_category_unmatched():
    """LLM 이 목록에 없는 카테고리를 창작했거나 비워뒀으면 그냥 포트폴리오를 안 붙인다 —

    재프롬프트하지 않는다(치명적 오류가 아니라 부가 정보 누락일 뿐이라서)."""
    facts = StaticFactSource([FACT])
    payload = {**VALID_PAYLOAD, "job_category": "존재하지않는카테고리"}
    portfolio = _portfolio_source({"개발자": "박예일_포트폴리오_풀스택.pdf"})
    gen = SimpleResumeGenerator(
        StubLLM(payloads=[payload]),
        UuidIdGen(),
        facts,
        _profile_source(),
        portfolio,
        StaticGuideSource(),
    )
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    assert draft.content["portfolio_filename"] == ""


async def test_generate_caps_career_blocks_per_entity():
    """회귀 테스트: 가이드 patch(자연어)로는 블록 개수를 못 줄인다 — 이 상한이 유일한 레버다

    (domain/resume_blocks.select_relevant_blocks, [[resume-block-count-cap]]). 회사 하나(acme)
    에 블록이 2개인데 상한을 1로 주면, LLM 이 둘 다에 대해 불릿을 써서 돌려줘도 관련도가 더
    높은 블록(FastAPI, job 설명과 겹침) 하나만 최종 이력서에 남아야 한다.
    """
    facts = StaticFactSource([FACT_BLOCK, FACT_BLOCK_DEVOPS])
    payload = {
        "summary": "FastAPI 경험을 살린 백엔드 엔지니어입니다",
        "highlights": [],
        "blocks": [
            {
                "block_id": "acme:payment-api",
                "bullets": [{"text": "결제 API 개발", "fact_ids": []}],
            },
            {"block_id": "acme:devops", "bullets": [{"text": "배포 자동화", "fact_ids": []}]},
        ],
    }
    gen = SimpleResumeGenerator(
        StubLLM(payloads=[payload]),
        UuidIdGen(),
        facts,
        _profile_source(),
        _portfolio_source(),
        StaticGuideSource(),
        max_career_blocks_per_entity=1,
    )
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    career = draft.content["career"]
    assert len(career) == 1  # 회사(entity) 자체는 그대로 1개
    blocks = career[0]["blocks"]
    assert [b["title"] for b in blocks] == ["결제 API 개발"]  # job 설명과 겹치는 블록만 남는다


async def test_generate_assembles_block_bullets_into_career_section():
    """블록 제목·기간·회사명은 결정론 코드가 채우고, LLM 은 블록 안 불릿만 쓴다."""
    facts = StaticFactSource([FACT_BLOCK])
    payload = {
        "summary": "충분히 긴 요약 문장입니다",
        "blocks": [
            {
                "block_id": "acme:payment-api",
                "bullets": [{"text": "결제 API 를 설계했다", "fact_ids": ["exp-block-1"]}],
            }
        ],
    }
    gen = SimpleResumeGenerator(
        StubLLM(payloads=[payload]),
        UuidIdGen(),
        facts,
        _profile_source(),
        _portfolio_source(),
        StaticGuideSource(),
    )
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    career = draft.content["career"]
    assert career[0]["company"] == "Acme(백엔드 인턴)"
    assert career[0]["blocks"][0]["title"] == "결제 API 개발"
    assert career[0]["blocks"][0]["tech_stack"] == ["FastAPI", "결제"]
    assert career[0]["blocks"][0]["bullets"][0]["fact_ids"] == ["exp-block-1"]
    assert draft.used_fact_ids == ["exp-block-1"]


async def test_generate_excludes_block_llm_skipped_from_career_section():
    """LLM 이 블록을 건너뛰면(bullets 0개) 빈 헤더로 남기지 않고 아예 뺀다.

    라이브 실측(2026-08-21): RESUME_MAX_* 상한을 안전판(20)으로 올려 LLM 판단에 맡기게
    되면서, LLM 이 "이 블록은 스킵"이라고 판단한 블록이 제목·기간·사용기술 태그만 남은 빈
    껍데기로 계속 렌더링되는 문제가 나왔다(adapters/resume/_assemble.py).
    """
    facts = StaticFactSource(
        [
            FACT_BLOCK,
            Fact(
                id="exp-block-2",
                user_id="u1",
                kind="experience",
                content="Acme 에서 배포 자동화를 구축했다.",
                keywords=["Docker"],
                entity="acme",
                entity_label="Acme(백엔드 인턴)",
                entity_period="2023.01 - 2023.12",
                block="devops",
                block_label="배포 자동화",
            ),
        ]
    )
    payload = {
        "summary": "충분히 긴 요약 문장입니다",
        "blocks": [
            {
                "block_id": "acme:payment-api",
                "bullets": [{"text": "결제 API 를 설계했다", "fact_ids": ["exp-block-1"]}],
            }
            # acme:devops 는 LLM 출력에서 아예 빠졌다(건너뛰기) — ai/prompts.py 가 허용한다.
        ],
    }
    gen = SimpleResumeGenerator(
        StubLLM(payloads=[payload]),
        UuidIdGen(),
        facts,
        _profile_source(),
        _portfolio_source(),
        StaticGuideSource(),
    )
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    career = draft.content["career"]
    assert career[0]["company"] == "Acme(백엔드 인턴)"  # 회사 헤더는 그대로 남는다
    assert [b["title"] for b in career[0]["blocks"]] == ["결제 API 개발"]  # devops 는 안 보인다


async def test_generate_reprompts_on_schema_violation_then_succeeds():
    """첫 응답이 스키마를 위반(창작 필드)해도 재프롬프트로 회복한다 (§5)."""
    facts = StaticFactSource([FACT])
    bad_payload = {**VALID_PAYLOAD, "made_up_field": "LLM이 창작한 필드"}
    llm = StubLLM(payloads=[bad_payload, VALID_PAYLOAD])
    gen = SimpleResumeGenerator(
        llm, UuidIdGen(), facts, _profile_source(), _portfolio_source(), StaticGuideSource()
    )
    draft = await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))
    assert draft.used_fact_ids == ["exp-1"]


async def test_generate_raises_after_exhausting_reprompts():
    facts = StaticFactSource([FACT])
    bad_payload = {**VALID_PAYLOAD, "made_up_field": "x"}
    llm = StubLLM(payloads=[bad_payload, bad_payload, bad_payload])
    gen = SimpleResumeGenerator(
        llm,
        UuidIdGen(),
        facts,
        _profile_source(),
        _portfolio_source(),
        StaticGuideSource(),
        max_reprompts=2,
    )
    with pytest.raises(LLMSchemaViolation):
        await gen.generate(GenerateResumeRequest(application_id="a1", user_id="u1", job=JOB))


async def test_review_fails_on_hallucinated_fact_id():
    """review 게이트 — 존재하지 않는 fact_id 를 인용한 초안은 통과하지 못한다."""
    facts = StaticFactSource([FACT])
    reviewer = SimpleResumeReviewer(facts)
    draft = ResumeDraft(
        resume_id="r1",
        content={
            "summary": "충분히 긴 요약 문장입니다",
            "highlights": [{"text": "지어낸 경력", "fact_ids": ["fake-id"]}],
        },
        used_fact_ids=["fake-id"],
    )
    verdict = await reviewer.review(ReviewRequest(draft=draft, job=JOB, user_id="u1"))
    assert verdict.passed is False
    assert any("존재하지 않는 fact_id" in issue for issue in verdict.issues)


async def test_review_passes_when_grounded():
    facts = StaticFactSource([FACT])
    reviewer = SimpleResumeReviewer(facts)
    draft = ResumeDraft(resume_id="r1", content=VALID_PAYLOAD, used_fact_ids=["exp-1"])
    verdict = await reviewer.review(ReviewRequest(draft=draft, job=JOB, user_id="u1"))
    assert verdict.passed is True
    assert verdict.issues == []
