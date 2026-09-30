"""저장소 port (§A3). 지원 건 상태 쓰기(`add`·`append_state`)는 `ApplicationService` 만 부른다 —
`tests/services/test_state_write_seal.py` 가 src 전체를 훑어 봉인한다(절대 규칙 6).
"""

from collections.abc import Collection
from types import TracebackType
from typing import Protocol, Self

from auto_apply.contracts.dto import ApplicationRecord, ApplicationSummary, PersistState
from auto_apply.domain.enums import ApplicationState
from auto_apply.ports.jobs import JobRepository, RunRepository
from auto_apply.ports.profile_store import (
    AnswerRepository,
    DocumentRepository,
    ExperienceRepository,
    ProfileRepository,
)


class ApplicationRepository(Protocol):
    async def add(self, record: ApplicationRecord, initial: PersistState) -> None:
        """새 지원 건 + 첫 이력 1행. 같은 id 가 이미 있으면 `InvalidInput`."""
        ...

    async def append_state(self, state: PersistState, *, expected: ApplicationState) -> None:
        """전이 1건을 이력에 append 하고 스냅샷을 바꾼다 (§A3). 합치지 않는다 — 최신 = 마지막 호출.

        현재 상태가 `expected` 가 아니면(그 사이 다른 쪽이 전이) 아무것도 쓰지 않고
        `InvalidTransition`, 없는 지원 건이면 `NotFound`. 표 검증은 호출자(서비스) 몫이다.
        """
        ...

    async def get(self, application_id: str) -> ApplicationRecord | None: ...

    async def history(self, application_id: str) -> list[PersistState]:
        """상태 전이 이력(오래된 순). 감사 로그 겸 테스트 검증용."""
        ...

    async def list_recent(self, limit: int = 10) -> list[ApplicationSummary]:
        """최근 갱신된 지원 건 상위 `limit`개, 각 건의 최신 상태만.

        "최근"의 기준은 구현마다 다르다 (sqlite 는 마지막 이력 행의 순번, memory 는 삽입 순서)
        — 정확한 정렬 보장이 필요한 용도가 아니라 강한 계약을 두지 않는다.
        """
        ...

    async def latest_states(self, application_ids: list[str]) -> dict[str, ApplicationState]:
        """주어진 id들 중 있는 것만, 최신 상태로. id 하나씩 `history()`를 부르면

        N+1 쿼리가 되므로 배치로 받는다.
        """
        ...

    async def in_states(self, states: Collection[ApplicationState]) -> dict[str, ApplicationState]:
        """최신 상태가 `states` 중 하나인 지원 건 전부 — 기동 시 크래시 복구(§A3)가 쓴다."""
        ...


class UnitOfWork(Protocol):
    # @property 로 선언한다. Protocol 의 일반 속성은 invariant 로 취급되어
    # 구현체가 더 구체적인 타입을 노출하면 타입 체크에 실패한다.
    @property
    def applications(self) -> ApplicationRepository: ...

    @property
    def profiles(self) -> ProfileRepository: ...

    @property
    def experiences(self) -> ExperienceRepository: ...

    @property
    def answers(self) -> AnswerRepository: ...

    @property
    def documents(self) -> DocumentRepository: ...

    @property
    def jobs(self) -> JobRepository: ...

    @property
    def runs(self) -> RunRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
