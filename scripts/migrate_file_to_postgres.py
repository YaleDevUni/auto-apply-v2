"""file 기반 var/ 데이터를 postgres 로 1회성 이관한다.

REPOSITORY=file → postgres 전환 백로그(repository-postgres-migration-backlog) 작업.
`UnitOfWork` port 가 "전체 조회" 를 요구하지 않아(§4.1, jobs.actionable() 은 필터링된
부분집합만 준다) 여기서는 var/ 파일을 직접 글롭해서 postgres 쪽 upsert 를 그대로 호출한다
— port 계약(멱등 upsert)에 기대므로 여러 번 실행해도 안전하다.

사용법:
    uv run python scripts/migrate_file_to_postgres.py [--data-dir var] [--database-url ...]
"""

import argparse
import asyncio
import json
from pathlib import Path

from auto_apply.adapters.repository.postgres import sqlalchemy_uow_factory
from auto_apply.contracts.dto import ApplicationAttempt, PersistState
from auto_apply.contracts.job import JobRecord


async def migrate(data_dir: Path, database_url: str) -> None:
    uow_factory = sqlalchemy_uow_factory(database_url)

    job_files = sorted((data_dir / "jobs").glob("*.json"))
    app_files = sorted((data_dir / "applications").glob("*.json"))
    attempt_files = sorted((data_dir / "attempts").glob("*.json"))

    async with uow_factory() as uow:
        for path in job_files:
            record = JobRecord.model_validate(json.loads(path.read_text()))
            await uow.jobs.upsert(record)
        print(f"jobs: {len(job_files)}건 이관")

        app_state_count = 0
        for path in app_files:
            for raw in json.loads(path.read_text()):
                await uow.applications.upsert_state(PersistState.model_validate(raw))
                app_state_count += 1
        print(f"applications: {len(app_files)}개 파일, {app_state_count}개 상태 레코드 이관")

        attempt_count = 0
        for path in attempt_files:
            for raw in json.loads(path.read_text()):
                await uow.attempts.record(ApplicationAttempt.model_validate(raw))
                attempt_count += 1
        print(f"attempts: {len(attempt_files)}개 파일, {attempt_count}개 시도 레코드 이관")

        await uow.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("var"))
    parser.add_argument(
        "--database-url",
        default="postgresql+asyncpg://auto_apply:auto_apply@localhost:5432/auto_apply",
    )
    args = parser.parse_args()
    asyncio.run(migrate(args.data_dir, args.database_url))


if __name__ == "__main__":
    main()
