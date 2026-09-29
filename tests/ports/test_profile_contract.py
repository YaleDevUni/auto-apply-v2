"""ProfileSource contract test — UoW 두 구현(memory·sqlite, tests/conftest.py)으로 돈다."""

import pytest

from auto_apply.adapters.profile.repository import RepositoryProfileSource
from auto_apply.contracts.profile import EducationEntry, LanguageEntry, Profile
from auto_apply.domain.errors import ProfileNotFound

PROFILE = Profile(
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


@pytest.fixture
async def source(uow_factory) -> RepositoryProfileSource:
    async with uow_factory() as uow:
        await uow.profiles.save(PROFILE)
        await uow.profiles.save(Profile(user_id="other-user", name="다른 사람"))
        await uow.commit()
    return RepositoryProfileSource(uow_factory)


async def test_get_returns_that_users_profile(source):
    profile = await source.get("u1")
    assert profile == PROFILE


async def test_get_raises_profile_not_found_for_unknown_user(source):
    with pytest.raises(ProfileNotFound):
        await source.get("nobody")
