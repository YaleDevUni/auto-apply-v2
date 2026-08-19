"""ProfileSource contract test."""

from pathlib import Path

import pytest

from auto_apply.adapters.profile.static import StaticProfileSource
from auto_apply.adapters.profile.yaml_source import YamlProfileSource
from auto_apply.contracts.profile import EducationEntry, LanguageEntry, Profile
from auto_apply.domain.errors import ProfileNotFound

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_YAML = """
- user_id: u1
  name: "홍길동"
  phone: "+821000000000"
  email: "hong@example.com"
  education:
    - school: "Example University"
      period: "2020.01 - 2024.02"
      status: "졸업"
      degree: "컴퓨터공학 학사"
  skills: [React, Python]
  languages:
    - name: "영어"
      level: "고급"
- user_id: other-user
  name: "다른 사람"
"""


def _write_sample_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "profile.yaml"
    path.write_text(SAMPLE_YAML, encoding="utf-8")
    return path


@pytest.fixture(params=["static", "yaml"])
def make_source(request: pytest.FixtureRequest, tmp_path):
    if request.param == "static":
        profile = Profile(
            user_id="u1",
            name="홍길동",
            phone="+821000000000",
            email="hong@example.com",
            education=[
                EducationEntry(
                    school="Example University",
                    period="2020.01 - 2024.02",
                    status="졸업",
                    degree="컴퓨터공학 학사",
                )
            ],
            skills=["React", "Python"],
            languages=[LanguageEntry(name="영어", level="고급")],
        )
        return StaticProfileSource([profile])
    return YamlProfileSource(_write_sample_yaml(tmp_path))


async def test_get_returns_that_users_profile(make_source):
    profile = await make_source.get("u1")
    assert profile.name == "홍길동"
    assert profile.skills == ["React", "Python"]
    assert profile.education[0].school == "Example University"
    assert profile.languages[0].level == "고급"


async def test_get_raises_profile_not_found_for_unknown_user(make_source):
    with pytest.raises(ProfileNotFound):
        await make_source.get("nobody")


async def test_repo_profile_example_yaml_is_valid():
    """config/profile.example.yaml(placeholder 템플릿, git 추적)이 스키마를 통과하는지 확인한다.

    실제 config/profile.yaml은 개인정보라 gitignore 대상이라 여기서 검증할 수 없다.
    """
    path = REPO_ROOT / "config" / "profile.example.yaml"
    profile = await YamlProfileSource(path).get("default")
    assert profile.name
