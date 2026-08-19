"""FactSource contract test."""

from pathlib import Path

import pytest

from auto_apply.adapters.facts.static import StaticFactSource
from auto_apply.adapters.facts.yaml_file import YamlFactSource
from auto_apply.contracts.fact import Fact

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_YAML = """
- id: f-1
  user_id: u1
  kind: experience
  content: "3년간 백엔드 개발"
  keywords: [백엔드, python]
  source: "이력서 v1"
- id: f-2
  user_id: other-user
  kind: skill
  content: "다른 사용자의 사실"
  keywords: [무관]
"""


def _write_sample_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "facts.yaml"
    path.write_text(SAMPLE_YAML, encoding="utf-8")
    return path


@pytest.fixture(params=["static", "yaml"])
def make_source(request: pytest.FixtureRequest, tmp_path):
    if request.param == "static":
        facts = [
            Fact(
                id="f-1",
                user_id="u1",
                kind="experience",
                content="3년간 백엔드 개발",
                keywords=["백엔드"],
            ),
            Fact(id="f-2", user_id="other-user", kind="skill", content="다른 사용자의 사실"),
        ]
        return StaticFactSource(facts)
    return YamlFactSource(_write_sample_yaml(tmp_path))


async def test_list_for_user_returns_only_that_users_facts(make_source):
    facts = await make_source.list_for_user("u1")
    assert [f.id for f in facts] == ["f-1"]
    assert all(isinstance(f, Fact) for f in facts)


async def test_list_for_user_returns_empty_for_unknown_user(make_source):
    assert await make_source.list_for_user("nobody") == []


async def test_yaml_source_parses_nested_fields(tmp_path):
    facts = await YamlFactSource(_write_sample_yaml(tmp_path)).list_for_user("u1")
    assert facts[0].keywords == ["백엔드", "python"]
    assert facts[0].source == "이력서 v1"


async def test_static_source_returns_empty_by_default():
    assert await StaticFactSource().list_for_user("u1") == []


async def test_repo_facts_example_yaml_is_valid():
    """config/facts.example.yaml(placeholder 템플릿, git 추적)이 스키마를 통과하는지 확인한다.

    실제 config/facts.yaml은 개인정보라 gitignore 대상이라 여기서 검증할 수 없다.
    """
    path = REPO_ROOT / "config" / "facts.example.yaml"
    facts = await YamlFactSource(path).list_for_user("default")
    assert len(facts) >= 1
    assert all(f.user_id == "default" for f in facts)
