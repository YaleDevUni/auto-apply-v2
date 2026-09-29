"""select_relevant_facts / ground_check — hallucination 회귀 테스트 (§A7, 절대 규칙 4)."""

from auto_apply.contracts.dto import ResumeDraft
from auto_apply.contracts.fact import Fact
from auto_apply.domain.resume_matching import ground_check, select_relevant_facts

FACT_BACKEND = Fact(
    id="f-backend",
    user_id="u1",
    kind="experience",
    content="백엔드 3년",
    keywords=["백엔드", "FastAPI"],
)
FACT_DESIGN = Fact(
    id="f-design", user_id="u1", kind="experience", content="디자인 인턴", keywords=["Figma", "UI"]
)


def test_select_relevant_facts_ranks_by_keyword_overlap():
    job_text = "백엔드 개발자 채용 - FastAPI 경험 우대"
    result = select_relevant_facts([FACT_DESIGN, FACT_BACKEND], job_text)
    assert result == [FACT_BACKEND]


def test_select_relevant_facts_falls_back_to_all_when_nothing_matches():
    """아무 것도 안 겹치면 걸러내지 않는다 — 잘못 걸러서 필요한 fact 가 안 보이는 게 더 나쁘다."""
    result = select_relevant_facts([FACT_DESIGN, FACT_BACKEND], "관련 없는 채용공고")
    assert {f.id for f in result} == {FACT_DESIGN.id, FACT_BACKEND.id}


def test_select_relevant_facts_respects_limit():
    facts = [
        Fact(id=f"f-{i}", user_id="u1", kind="skill", content=f"skill {i}", keywords=["python"])
        for i in range(10)
    ]
    result = select_relevant_facts(facts, "python 개발자", limit=3)
    assert len(result) == 3


def test_ground_check_passes_when_every_highlight_cites_known_facts():
    highlight = {"text": "백엔드 경력", "fact_ids": ["f-backend"]}
    draft = ResumeDraft(
        resume_id="r1",
        content={"summary": "요약", "highlights": [highlight]},
        used_fact_ids=["f-backend"],
    )
    assert ground_check(draft, [FACT_BACKEND]) == []


def test_ground_check_flags_highlight_without_fact_ids():
    """근거 fact_id 가 하나도 없는 서술 — 지어냈을 위험이 있는 서술."""
    draft = ResumeDraft(
        resume_id="r1",
        content={"summary": "요약", "highlights": [{"text": "10년간 CEO로 재직", "fact_ids": []}]},
    )
    issues = ground_check(draft, [FACT_BACKEND])
    assert any("근거 fact 없는 서술" in i for i in issues)


def test_ground_check_flags_fabricated_fact_id():
    """실제로는 존재하지 않는 fact_id 를 인용 — 명백한 hallucination."""
    draft = ResumeDraft(
        resume_id="r1",
        content={
            "summary": "요약",
            "highlights": [{"text": "화성 이주 프로젝트 리드", "fact_ids": ["f-nonexistent"]}],
        },
        used_fact_ids=["f-nonexistent"],
    )
    issues = ground_check(draft, [FACT_BACKEND])
    assert any("존재하지 않는 fact_id" in i for i in issues)
    # used_fact_ids 경로도 같은 위반을 잡는다(두 경로가 서로 다른 계약을 지켜 중복은 허용).
    assert sum("f-nonexistent" in i for i in issues) >= 1


def test_ground_check_flags_hallucinated_bullet_nested_under_career_block():
    """career[].blocks[].bullets 도 top-level highlights 와 같은 기준으로 검사한다."""
    draft = ResumeDraft(
        resume_id="r1",
        content={
            "summary": "요약",
            "career": [
                {
                    "company": "Acme",
                    "blocks": [
                        {"title": "블록", "bullets": [{"text": "지어낸 성과", "fact_ids": []}]}
                    ],
                }
            ],
        },
    )
    issues = ground_check(draft, [FACT_BACKEND])
    assert any("근거 fact 없는 서술" in i for i in issues)
