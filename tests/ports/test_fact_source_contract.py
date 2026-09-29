"""FactSource contract test — 저장소의 Experience 가 이력서 파이프라인용 Fact 로 나온다 (§A7).

구현은 `RepositoryFactSource` 하나지만 UoW 두 구현(memory·sqlite, tests/conftest.py)으로 돈다.
"""

from auto_apply.adapters.facts.repository import RepositoryFactSource
from auto_apply.contracts.experience import Experience, ExperienceFact
from auto_apply.contracts.fact import Fact
from auto_apply.domain.enums import ExperienceKind


def _exp(exp_id: str, user_id: str, fact_id: str) -> Experience:
    return Experience(
        id=exp_id,
        user_id=user_id,
        kind=ExperienceKind.PROJECT,
        name=f"프로젝트 {exp_id}",
        skills=["Python"],
        facts=[ExperienceFact(id=fact_id, text="3년간 백엔드 개발")],
    )


async def _seed(uow_factory, *experiences: Experience) -> None:
    async with uow_factory() as uow:
        for exp in experiences:
            await uow.experiences.save(exp)
        await uow.commit()


async def test_list_for_user_returns_only_that_users_facts(uow_factory):
    await _seed(uow_factory, _exp("p1", "u1", "f-1"), _exp("p2", "other", "f-2"))
    facts = await RepositoryFactSource(uow_factory).list_for_user("u1")
    assert [f.id for f in facts] == ["f-1"]
    assert all(isinstance(f, Fact) for f in facts)
    assert facts[0].keywords == ["Python"]
    assert facts[0].entity == "p1"


async def test_list_follows_experience_order(uow_factory):
    await _seed(uow_factory, _exp("p2", "u1", "f-2"), _exp("p1", "u1", "f-1"))
    facts = await RepositoryFactSource(uow_factory).list_for_user("u1")
    assert [f.id for f in facts] == ["f-2", "f-1"]


async def test_list_for_user_returns_empty_for_unknown_user(uow_factory):
    assert await RepositoryFactSource(uow_factory).list_for_user("nobody") == []


async def test_edits_are_visible_on_next_call(uow_factory):
    """캐시하지 않는다 — 경험을 고치면 바로 다음 생성부터 보여야 한다."""
    source = RepositoryFactSource(uow_factory)
    await _seed(uow_factory, _exp("p1", "u1", "f-1"))
    assert [f.id for f in await source.list_for_user("u1")] == ["f-1"]
    await _seed(uow_factory, _exp("p1", "u1", "f-9"))
    assert [f.id for f in await source.list_for_user("u1")] == ["f-9"]
