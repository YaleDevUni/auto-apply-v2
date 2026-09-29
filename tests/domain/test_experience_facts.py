"""Experience → Fact 변환이 v2 블록 조립 계약을 그대로 만족하는지 (§A7)."""

import pytest
from pydantic import ValidationError

from auto_apply.contracts.experience import Experience, ExperienceFact, ExperienceSection
from auto_apply.domain.enums import ExperienceKind
from auto_apply.domain.experience_facts import experiences_to_facts
from auto_apply.domain.resume_blocks import group_facts_for_resume

COMPANY = Experience(
    id="acme",
    user_id="u1",
    kind=ExperienceKind.COMPANY,
    name="Acme",
    role="백엔드 인턴",
    period="2023.01 - 2023.12",
    skills=["Python"],
    facts=[ExperienceFact(id="acme-role", text="Acme 백엔드 인턴")],
    sections=[
        ExperienceSection(
            key="pay",
            title="결제 API 개발",
            period="2023.01 - 2023.06",
            facts=[ExperienceFact(id="acme-pay", text="결제 API 설계", skills=["FastAPI"])],
        ),
        ExperienceSection(
            key="ops",
            title="배포 자동화",
            facts=[ExperienceFact(id="acme-ops", text="CI/CD 구축")],
        ),
    ],
)
PROJECT = Experience(
    id="proj-x",
    user_id="u1",
    kind=ExperienceKind.PROJECT,
    name="X 프로젝트",
    period="2024.01 - 2024.02",
    url="https://github.com/example/x",
    skills=["React"],
    facts=[ExperienceFact(id="x-1", text="X 를 개발했다")],
)
ACTIVITY = Experience(
    id="club",
    user_id="u1",
    kind=ExperienceKind.ACTIVITY,
    name="동아리",
    facts=[ExperienceFact(id="club-1", text="동아리 회장")],
)


def test_sections_become_career_blocks_under_one_entity():
    blocks = group_facts_for_resume(experiences_to_facts([COMPANY]))
    assert [b.id for b in blocks] == ["acme:pay", "acme:ops"]
    pay, ops = blocks
    assert pay.kind == "career"
    assert pay.entity_label == "Acme(백엔드 인턴)"
    assert pay.entity_period == "2023.01 - 2023.12"
    assert (pay.title, pay.period) == ("결제 API 개발", "2023.01 - 2023.06")
    assert pay.tech_stack == ["FastAPI"]
    # 섹션 기간이 비면 엔티티 기간, fact skills 가 비면 Experience.skills 를 물려받는다
    assert ops.period == "2023.01 - 2023.12"
    assert ops.tech_stack == ["Python"]


def test_experience_without_sections_is_one_main_block():
    [block] = group_facts_for_resume(experiences_to_facts([PROJECT]))
    assert block.id == "proj-x:main"
    assert block.kind == "project"
    assert block.title == "X 프로젝트"
    assert block.entity_url == "https://github.com/example/x"


def test_company_without_sections_titles_block_by_role():
    company = COMPANY.model_copy(update={"sections": []})
    [block] = group_facts_for_resume(experiences_to_facts([company]))
    assert (block.entity_label, block.title) == ("Acme(백엔드 인턴)", "백엔드 인턴")


def test_activity_facts_are_citable_but_form_no_block():
    facts = experiences_to_facts([ACTIVITY])
    assert [(f.id, f.kind, f.entity) for f in facts] == [("club-1", "activity", None)]
    assert group_facts_for_resume(facts) == []


def test_order_and_ids_are_preserved():
    facts = experiences_to_facts([PROJECT, COMPANY, ACTIVITY])
    assert [f.id for f in facts] == ["x-1", "acme-role", "acme-pay", "acme-ops", "club-1"]
    assert {f.user_id for f in facts} == {"u1"}


def test_duplicate_fact_ids_or_section_keys_are_rejected():
    dup_fact = ExperienceFact(id="acme-role", text="중복")
    with pytest.raises(ValidationError):
        COMPANY.model_validate({**COMPANY.model_dump(), "facts": [dup_fact.model_dump()] * 2})
    with pytest.raises(ValidationError):
        COMPANY.model_validate(
            {**COMPANY.model_dump(), "sections": [COMPANY.sections[0].model_dump()] * 2}
        )
