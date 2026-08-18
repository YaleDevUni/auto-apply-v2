from types import TracebackType
from typing import Protocol, Self

from auto_apply.contracts.dto import PersistState


class ApplicationRepository(Protocol):
    async def upsert_state(self, state: PersistState) -> None:
        """(application_id, workflow_run_id, state) 기준 멱등 upsert (§4.1)."""
        ...


class UnitOfWork(Protocol):
    applications: ApplicationRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
