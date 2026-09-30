from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

from auto_apply.adapters.repository.memory_profile import (
    InMemoryAnswerRepository,
    InMemoryDocumentRepository,
    InMemoryExperienceRepository,
    InMemoryProfileRepository,
)
from auto_apply.contracts.dto import ApplicationRecord, ApplicationSummary, PersistState
from auto_apply.contracts.experience import Experience
from auto_apply.contracts.knowledge import Answer, DocumentMeta
from auto_apply.contracts.profile import Profile
from auto_apply.domain.enums import ApplicationState
from auto_apply.domain.errors import InvalidInput, InvalidTransition, NotFound

Rows = dict[str, list[PersistState]]


@dataclass
class InMemoryDatabase:
    """UoW 여러 개가 공유하는 "DB". 트랜잭션이 없다 — 쓰는 즉시 보이고 롤백되지 않는다."""

    applications: Rows = field(default_factory=dict)
    records: dict[str, ApplicationRecord] = field(default_factory=dict)
    profiles: dict[str, Profile] = field(default_factory=dict)
    experiences: dict[str, Experience] = field(default_factory=dict)
    answers: dict[str, Answer] = field(default_factory=dict)
    documents: dict[str, DocumentMeta] = field(default_factory=dict)


def _summary(application_id: str, latest: PersistState) -> ApplicationSummary:
    return ApplicationSummary(
        application_id=application_id,
        state=latest.state,
        reason=latest.reason,
        at=latest.at,
        submitted_at=latest.submitted_at,
    )


class InMemoryApplicationRepository:
    def __init__(self, rows: Rows, records: dict[str, ApplicationRecord]) -> None:
        self._rows = rows
        self._records = records

    async def add(self, record: ApplicationRecord, initial: PersistState) -> None:
        if record.application_id in self._records:
            raise InvalidInput(f"이미 있는 지원 건: {record.application_id}")
        self._records[record.application_id] = record
        self._rows[record.application_id] = [initial]

    async def append_state(self, state: PersistState, *, expected: ApplicationState) -> None:
        history = self._rows.get(state.application_id)
        if not history:
            raise NotFound(state.application_id)
        if history[-1].state is not expected:
            raise InvalidTransition(history[-1].state, state.state, f"expected {expected}")
        history.append(state)

    async def get(self, application_id: str) -> ApplicationRecord | None:
        return self._records.get(application_id)

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
    def __init__(self, db: InMemoryDatabase) -> None:
        self.applications = InMemoryApplicationRepository(db.applications, db.records)
        self.profiles = InMemoryProfileRepository(db.profiles)
        self.experiences = InMemoryExperienceRepository(db.experiences)
        self.answers = InMemoryAnswerRepository(db.answers)
        self.documents = InMemoryDocumentRepository(db.documents)

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
