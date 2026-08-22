"""schedule_config.py — DB(ScheduleConfig)와 Temporal Schedule을 잇는 오케스트레이션.

§ apply-schedule. Temporal Client 는 `tests/telegram/test_agent_tools_schedule.py`의
`_FakeClient`를 그대로 재사용한다 — 여기서 다시 만들지 않는다.
"""

from auto_apply.config import Settings
from auto_apply.contracts.dto import ScheduleConfig
from auto_apply.schedule_config import ensure, load_or_seed, push, save_and_push
from tests.conftest import Harness
from tests.telegram.test_agent_tools_schedule import _FakeClient


def _container(**settings_overrides):
    settings = Settings(storage="memory", llm_provider="stub", **settings_overrides)
    return Harness().container(settings=settings)


async def test_load_or_seed_returns_existing_db_row_without_touching_env():
    c = _container(job_collection_cron="0 9 * * *")
    async with c.uow() as uow:
        await uow.schedule_config.set(
            ScheduleConfig(target="collection", hour=13, minute=15, platforms=["wanted"])
        )
        await uow.commit()

    config = await load_or_seed(c, "collection")

    assert (config.hour, config.minute, config.platforms) == (13, 15, ["wanted"])


async def test_load_or_seed_seeds_collection_from_env_when_db_empty():
    c = _container(
        job_collection_cron="0 9 * * *", job_collection_platforms="wanted, saramin , jasoseol"
    )

    config = await load_or_seed(c, "collection")

    assert config.target == "collection"
    assert (config.hour, config.minute) == (9, 0)
    assert config.platforms == ["wanted", "saramin", "jasoseol"]
    # 시드는 저장까지 된다 — 다음 조회부터는 .env 를 다시 안 본다.
    async with c.uow() as uow:
        assert await uow.schedule_config.get("collection") == config


async def test_load_or_seed_seeds_apply_from_env_when_db_empty():
    c = _container(apply_schedule_cron="0 10 * * *", apply_schedule_count=3)

    config = await load_or_seed(c, "apply")

    assert (config.hour, config.minute, config.count) == (10, 0, 3)


async def test_push_creates_collection_schedule_with_correct_cron_and_platforms():
    client = _FakeClient()
    config = ScheduleConfig(target="collection", hour=8, minute=5, platforms=["wanted"])

    outcome = await push(client, config)

    assert outcome == "created"
    handle = client.get_schedule_handle("job-collection-schedule")
    assert handle.exists is True
    assert handle.schedule.spec.cron_expressions == ["5 8 * * *"]


async def test_push_creates_apply_schedule_with_correct_cron_and_count():
    client = _FakeClient()
    config = ScheduleConfig(target="apply", hour=22, minute=0, count=9)

    outcome = await push(client, config)

    assert outcome == "created"
    handle = client.get_schedule_handle("apply-intake-schedule")
    assert handle.schedule.spec.cron_expressions == ["0 22 * * *"]


async def test_save_and_push_persists_before_pushing():
    c = _container()
    client = _FakeClient()
    config = ScheduleConfig(target="apply", hour=16, minute=45, count=2)

    await save_and_push(c, client, config)

    async with c.uow() as uow:
        assert await uow.schedule_config.get("apply") == config
    assert client.get_schedule_handle("apply-intake-schedule").exists is True


async def test_ensure_seeds_and_pushes_in_one_call():
    c = _container(apply_schedule_cron="0 10 * * *", apply_schedule_count=4)
    client = _FakeClient()

    outcome, config = await ensure(c, client, "apply")

    assert outcome == "created"
    assert config.count == 4
    assert client.get_schedule_handle("apply-intake-schedule").exists is True
