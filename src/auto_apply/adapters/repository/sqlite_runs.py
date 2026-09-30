"""SQLite run 기록 (§A3 `runs`). 에이전트 세션 1회 = 1행."""

from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply.adapters.repository.models import RunRow
from auto_apply.adapters.repository.sqlite_jobs import from_db, to_db
from auto_apply.contracts._base import ensure_identifier_free
from auto_apply.domain.enums import RunKind, RunStatus
from auto_apply.domain.errors import InvalidInput, NotFound
from auto_apply.domain.unique_identifiers import reject_unique_identifiers
from auto_apply.ports.jobs import RunRecord

_RUN_COLUMNS = (
    RunRow.id,
    RunRow.application_id,
    RunRow.kind,
    RunRow.status,
    RunRow.started_at,
    RunRow.finished_at,
    RunRow.result,
    RunRow.error,
    RunRow.input_tokens,
    RunRow.output_tokens,
    RunRow.transcript_path,
)


class SqliteRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, run: RunRecord) -> None:
        ensure_identifier_free(run)
        if await self.get(run.run_id) is not None:
            raise InvalidInput(f"이미 있는 run: {run.run_id}")
        await self._session.execute(
            insert(RunRow).values(
                id=run.run_id,
                application_id=run.application_id,
                kind=str(run.kind),
                status=str(RunStatus.RUNNING),
                started_at=to_db(run.started_at),
            )
        )

    async def finish(
        self,
        run_id: str,
        status: RunStatus,
        *,
        at: datetime,
        result: str | None = None,
        error: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        transcript_path: str | None = None,
    ) -> None:
        if status is RunStatus.RUNNING:
            raise InvalidInput("run 을 RUNNING 으로 닫을 수 없다")
        if error is not None:
            reject_unique_identifiers(error, where="run.error")
        res = await self._session.execute(
            update(RunRow)
            .where(RunRow.id == run_id, RunRow.status == str(RunStatus.RUNNING))
            .values(
                status=str(status),
                finished_at=to_db(at),
                result=result,
                error=error,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                transcript_path=transcript_path,
            )
        )
        if res.rowcount == 0:  # type: ignore[attr-defined]
            if await self.get(run_id) is None:
                raise NotFound(run_id)
            raise InvalidInput(f"이미 닫힌 run: {run_id}")

    async def interrupt_running(self, *, at: datetime) -> list[str]:
        ids = list(
            await self._session.scalars(
                select(RunRow.id).where(RunRow.status == str(RunStatus.RUNNING))
            )
        )
        await self._session.execute(
            update(RunRow)
            .where(RunRow.id.in_(ids))
            .values(status=str(RunStatus.INTERRUPTED), finished_at=to_db(at))
        )
        return ids

    async def get(self, run_id: str) -> RunRecord | None:
        row = (
            await self._session.execute(select(*_RUN_COLUMNS).where(RunRow.id == run_id))
        ).one_or_none()
        if row is None:
            return None
        return RunRecord(
            run_id=row.id,
            application_id=row.application_id,
            kind=RunKind(row.kind),
            status=RunStatus(row.status),
            started_at=row.started_at.replace(tzinfo=UTC),
            finished_at=from_db(row.finished_at),
            result=row.result,
            error=row.error,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            transcript_path=row.transcript_path,
        )
