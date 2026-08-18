from types import TracebackType
from typing import Self

from auto_apply.contracts.dto import PersistState

Rows = dict[str, list[PersistState]]


class InMemoryApplicationRepository:
    def __init__(self, rows: Rows) -> None:
        self._rows = rows

    async def upsert_state(self, state: PersistState) -> None:
        history = self._rows.setdefault(state.application_id, [])
        # 멱등: 같은 (run_id, state) 가 이미 있으면 값만 갱신한다
        for i, existing in enumerate(history):
            if existing.workflow_run_id == state.workflow_run_id and existing.state == state.state:
                history[i] = state
                return
        history.append(state)

    async def history(self, application_id: str) -> list[PersistState]:
        return list(self._rows.get(application_id, []))


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


def in_memory_uow(rows: Rows | None = None) -> tuple[Rows, "type[InMemoryUnitOfWork]"]:
    """테스트용 헬퍼. rows 를 공유하는 UoW 팩토리를 만든다."""
    shared: Rows = rows if rows is not None else {}
    return shared, InMemoryUnitOfWork
