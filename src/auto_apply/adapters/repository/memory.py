from types import TracebackType
from typing import Self

from auto_apply.contracts.dto import ApplicationSummary, PersistState
from auto_apply.domain.enums import ApplicationState

Rows = dict[str, list[PersistState]]


def _summary(application_id: str, latest: PersistState) -> ApplicationSummary:
    return ApplicationSummary(
        application_id=application_id,
        state=latest.state,
        reason=latest.reason,
        scheduled_at=latest.scheduled_at,
        submitted_at=latest.submitted_at,
    )


class InMemoryApplicationRepository:
    def __init__(self, rows: Rows) -> None:
        self._rows = rows

    async def upsert_state(self, state: PersistState) -> None:
        self._rows.setdefault(state.application_id, []).append(state)

    async def history(self, application_id: str) -> list[PersistState]:
        return list(self._rows.get(application_id, []))

    async def list_recent(self, limit: int = 10) -> list[ApplicationSummary]:
        # 정렬 순서는 보장하지 않는다(테스트 대역, ports/repository.py 참고) — dict 삽입 순서를
        # 최신순처럼 뒤집어 보여줄 뿐이다.
        app_ids = list(reversed(self._rows))[:limit]
        return [
            _summary(app_id, self._rows[app_id][-1]) for app_id in app_ids if self._rows[app_id]
        ]

    async def latest_states(self, application_ids: list[str]) -> dict[str, ApplicationState]:
        return {aid: self._rows[aid][-1].state for aid in application_ids if self._rows.get(aid)}


class InMemoryUnitOfWork:
    def __init__(self, rows: Rows) -> None:
        self.applications = InMemoryApplicationRepository(rows)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None
