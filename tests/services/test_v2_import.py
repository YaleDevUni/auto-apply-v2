"""v2 `config/*.yaml` 임포터 (T1.3).

같은 초안 경로로 들어오고, 확정하면 v2 이력서 블록 구조가 유지된다.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_apply.domain.enums import ExperienceKind
from auto_apply.domain.errors import InvalidInput
from auto_apply.domain.experience_facts import experiences_to_facts
from auto_apply.services.profile_draft_types import DraftSelection, DraftSource, ProfileField
from auto_apply.services.v2_import import import_v2_yaml
from tests.services.fakes import make_draft_service

V2 = Path(__file__).parents[1] / "fixtures" / "v2_config"
PROFILE_YAML = (V2 / "profile.yaml").read_text(encoding="utf-8")
FACTS_YAML = (V2 / "facts.yaml").read_text(encoding="utf-8")


def test_example_files_map_to_draft():
    draft = import_v2_yaml(PROFILE_YAML, FACTS_YAML)

    p = draft.profile
    assert (p.name, p.email) == ("홍길동", "example@example.com")
    assert p.education[0].school == "예시대학교"
    assert p.skills == ["React", "Node.js", "Python", "Docker", "AWS"]
    assert p.additional.military is None  # v2 에 없던 칸은 "모름"

    company, project, skill = draft.experiences
    assert (company.kind, company.name, company.role) == (
        ExperienceKind.COMPANY,
        "Acme",
        "백엔드 엔지니어",
    )
    assert company.period == "2022.01 - 2023.12"
    assert [f.text for f in company.facts] == [
        "예시: Acme(스타트업)에서 2022.01~2023.12 백엔드 엔지니어로 근무했다."
    ]
    [section] = company.sections
    assert (section.title, section.period) == ("결제 API 설계·운영", "2022.01 - 2022.12")
    assert section.facts[0].skills == ["FastAPI", "결제", "API", "백엔드"]

    # v2 개인 프로젝트 관례(block=main 하나)는 섹션 없이 편다.
    assert (project.kind, project.name, project.period) == (
        ExperienceKind.PROJECT,
        "실시간 채팅 서비스",
        "2024.01 - 2024.02",
    )
    assert project.url == "https://github.com/example/realtime-chat"
    assert project.sections == [] and len(project.facts) == 1

    assert (skill.kind, skill.name) == (ExperienceKind.ACTIVITY, "skill")
    assert skill.skills == ["Python", "PostgreSQL", "Docker"]


async def test_import_confirm_keeps_v2_resume_blocks(uow_factory):
    """가져와 확정한 경험을 이력서 파이프라인용 Fact 로 펴면 v2 의 entity/block 구조가 그대로다."""
    svc = make_draft_service(uow_factory)
    draft = await svc.import_v2("u1", PROFILE_YAML, FACTS_YAML)
    assert draft.source is DraftSource.V2_YAML

    result = await svc.confirm(
        "u1",
        draft.id,
        DraftSelection(profile_fields=list(ProfileField), experience_indexes=[0, 1, 2]),
    )

    assert result.profile is not None and result.profile.name == "홍길동"
    facts = experiences_to_facts(result.experiences)
    acme = [f for f in facts if f.entity == result.experiences[0].id]
    assert {f.entity_label for f in acme} == {"Acme(백엔드 엔지니어)"}
    assert [f.block_label for f in acme] == [None, "결제 API 설계·운영"]
    chat = [f for f in facts if f.entity == result.experiences[1].id]
    assert [(f.block, f.block_label, f.entity_url) for f in chat] == [
        ("main", "실시간 채팅 서비스", "https://github.com/example/realtime-chat")
    ]


def test_only_one_file_is_enough():
    assert import_v2_yaml(PROFILE_YAML, None).experiences == []
    assert import_v2_yaml(None, FACTS_YAML).profile.name == ""
    # 목록 대신 매핑 하나로 적은 profile.yaml 도 받는다.
    single = "user_id: default\nname: 홍길동\n"
    assert import_v2_yaml(single, "").profile.name == "홍길동"


@pytest.mark.parametrize(
    ("profile_yaml", "facts_yaml"),
    [
        (None, None),
        ("", "   \n"),
        ("just a string", None),
        ("- user_id: a\n  name: A\n- user_id: b\n  name: B\n", None),  # 사용자 둘
        ("- user_id: a\n  name: A\n", "- {id: f, user_id: b, kind: skill, content: x}\n"),
        # billion laughs 식 별칭 확장
        (None, "a: &a [x, x]\nb: &b [*a, *a]\nc: [*b, *b]\n"),
    ],
    ids=["none", "blank", "scalar", "two-users", "mixed-users", "alias"],
)
def test_rejected_inputs(profile_yaml, facts_yaml):
    with pytest.raises(InvalidInput):
        import_v2_yaml(profile_yaml, facts_yaml)


def test_malformed_yaml_does_not_echo_content():
    with pytest.raises(InvalidInput) as exc:
        import_v2_yaml("- user_id: a\n  name: [비밀내용\n", None)
    assert "비밀내용" not in str(exc.value)
    assert "행" in str(exc.value)


def test_schema_mismatch_and_identifiers_rejected():
    with pytest.raises(ValidationError):
        import_v2_yaml("- user_id: a\n  name: A\n  github: x\n", None)  # v2 에 없던 키
    with pytest.raises(ValidationError) as exc:
        import_v2_yaml("- user_id: a\n  name: A\n  phone: 900101-1234567\n", None)
    assert "900101" not in str(exc.value)
