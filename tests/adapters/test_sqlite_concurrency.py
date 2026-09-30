"""SQLite 동시 쓰기 (§A9) — API 와 JobRunner 가 한 파일에 동시에 읽고-쓰는 트랜잭션을 연다.

DEFERRED 면 동시에 연 트랜잭션들이 같은 "가장 오래된 행"을 읽고, 조건부 UPDATE 에서 하나만
이겨 나머지는 빈손으로 돌아가거나 쓰기 승격 교착으로 "database is locked" 가 난다(실측).
`BEGIN IMMEDIATE` 면 뒤에 온 쪽이 잠금을 기다렸다가 다음 행을 읽는다.
"""

import asyncio
from datetime import UTC, datetime

from auto_apply.adapters.repository.sqlite import sqlite_uow_factory
from auto_apply.contracts.jobs import JobKind, JobRecord

T0 = datetime(2026, 10, 1, tzinfo=UTC)


async def test_concurrent_claims_each_get_their_own_job(sqlite_url):
    uow = sqlite_uow_factory(sqlite_url)
    async with uow() as u:
        for i in range(20):
            await u.jobs.enqueue(
                JobRecord(
                    job_id=f"j{i}",
                    kind=JobKind.REFLECT,
                    application_id=None,
                    created_at=T0,
                    run_after=T0,
                )
            )
        await u.commit()

    async def claim() -> str | None:
        async with uow() as u:
            job = await u.jobs.claim_next(frozenset({JobKind.REFLECT}), now=T0)
            await asyncio.sleep(0)  # 읽은 뒤 다른 트랜잭션이 끼어들 틈
            await u.commit()
        return None if job is None else job.job_id

    claimed = await asyncio.gather(*(claim() for _ in range(20)))
    assert sorted(c for c in claimed if c) == sorted(f"j{i}" for i in range(20))
