"""group_facts_for_resume / select_relevant_blocks — 경력·프로젝트 블록 조립 (§2.3)."""

from auto_apply.contracts.fact import Fact
from auto_apply.domain.resume_blocks import group_facts_for_resume, select_relevant_blocks

FACT_ROLE = Fact(
    id="exp-role",
    user_id="u1",
    kind="experience",
    content="Acme 에서 인턴으로 근무했다.",
    entity="acme",
    entity_label="Acme(백엔드 인턴)",
    entity_period="2023.01 - 2023.12",
)
FACT_API = Fact(
    id="exp-api",
    user_id="u1",
    kind="experience",
    content="REST API 를 설계·구현했다.",
    keywords=["FastAPI", "REST"],
    entity="acme",
    block="api",
    block_label="API 개발",
    block_period="2023.01 - 2023.06",
)
FACT_DEVOPS = Fact(
    id="exp-devops",
    user_id="u1",
    kind="experience",
    content="배포 파이프라인을 구축했다.",
    keywords=["Docker", "CI/CD"],
    entity="acme",
    block="devops",
    block_label="배포 자동화",
    block_period="2023.07 - 2023.12",
)
FACT_PROJ_SUMMARY = Fact(
    id="proj-x-summary",
    user_id="u1",
    kind="project",
    content="X 프로젝트를 개발했다.",
    keywords=["React"],
    entity="proj-x",
    block="main",
    block_label="X 프로젝트",
    block_period="2024.01 - 2024.02",
)
FACT_PROJ_DETAIL = Fact(
    id="proj-x-detail",
    user_id="u1",
    kind="project",
    content="React 로 프론트를 구현했다.",
    keywords=["React", "TypeScript"],
    entity="proj-x",
    block="main",
)
FACT_PROJ_OTHER = Fact(
    id="proj-y-summary",
    user_id="u1",
    kind="project",
    content="Y 프로젝트를 개발했다.",
    keywords=["디자인", "Figma"],
    entity="proj-y",
    block="main",
    block_label="Y 프로젝트",
    block_period="2024.03 - 2024.04",
)
FACT_SKILL_NO_ENTITY = Fact(
    id="skill-1", user_id="u1", kind="skill", content="Python 능숙", keywords=["Python"]
)


def test_group_facts_for_resume_builds_company_header_and_sub_blocks():
    blocks = group_facts_for_resume([FACT_ROLE, FACT_API, FACT_DEVOPS])
    assert [b.id for b in blocks] == ["acme:api", "acme:devops"]
    api_block = blocks[0]
    assert api_block.kind == "career"
    assert api_block.entity_label == "Acme(백엔드 인턴)"
    assert api_block.entity_period == "2023.01 - 2023.12"
    assert api_block.title == "API 개발"
    assert api_block.period == "2023.01 - 2023.06"
    assert api_block.tech_stack == ["FastAPI", "REST"]


def test_group_facts_for_resume_merges_facts_sharing_same_block():
    blocks = group_facts_for_resume([FACT_PROJ_SUMMARY, FACT_PROJ_DETAIL])
    assert len(blocks) == 1
    block = blocks[0]
    assert block.kind == "project"
    assert block.title == "X 프로젝트"
    assert [f.id for f in block.facts] == ["proj-x-summary", "proj-x-detail"]
    # 헤더 fact 가 없는 프로젝트는 블록 자신의 title/period 가 entity label/period 로도 쓰인다.
    assert block.entity_label == "X 프로젝트"
    assert block.tech_stack == ["React", "TypeScript"]


def test_group_facts_for_resume_excludes_facts_without_entity():
    blocks = group_facts_for_resume([FACT_SKILL_NO_ENTITY])
    assert blocks == []


def test_select_relevant_blocks_keeps_all_career_and_ranks_projects():
    blocks = group_facts_for_resume(
        [FACT_ROLE, FACT_API, FACT_DEVOPS, FACT_PROJ_SUMMARY, FACT_PROJ_DETAIL, FACT_PROJ_OTHER]
    )
    selected = select_relevant_blocks(blocks, "React 프론트엔드 개발자 채용", max_projects=1)
    ids = {b.id for b in selected}
    assert {"acme:api", "acme:devops"} <= ids  # 경력은 전부 유지
    assert "proj-x:main" in ids  # React 겹침 점수가 더 높은 프로젝트만 선택
    assert "proj-y:main" not in ids
