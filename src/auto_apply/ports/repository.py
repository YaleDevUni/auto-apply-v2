from types import TracebackType
from typing import Protocol, Self

from auto_apply.contracts.dto import ApplicationSummary, PersistState
from auto_apply.domain.enums import ApplicationState


class ApplicationRepository(Protocol):
    async def upsert_state(self, state: PersistState) -> None:
        """전이 1건을 이력에 append 한다 (§A3). 이름과 달리 합치지 않는다 — 최신 = 마지막 호출.

        같은 run 안에서도 A→B→A 로 되돌아올 수 있어 (run, state) 로 중복을 거르지 않는다.
        전이 검증·중복 차단은 `ApplicationService.transition()`(M4) 몫이다.
        """
        ...

    async def history(self, application_id: str) -> list[PersistState]:
        """상태 전이 이력. 감사 로그 겸 테스트 검증용."""
        ...

    async def list_recent(self, limit: int = 10) -> list[ApplicationSummary]:
        """최근 갱신된 지원 건 상위 `limit`개, 각 건의 최신 상태만.

        "최근"의 기준은 구현마다 다르다 (sqlite 는 마지막 이력 행의 순번, memory 는 삽입 순서)
        — 정확한 정렬 보장이 필요한 용도가 아니라 강한 계약을 두지 않는다.
        """
        ...

    async def latest_states(self, application_ids: list[str]) -> dict[str, ApplicationState]:
        """주어진 id들 중 이력이 있는 것만, 최신 상태로. id 하나씩 `history()`를 부르면

        N+1 쿼리가 되므로 배치로 받는다.
        """
        ...


class UnitOfWork(Protocol):
    # @property 로 선언한다. Protocol 의 일반 속성은 invariant 로 취급되어
    # 구현체가 더 구체적인 타입을 노출하면 타입 체크에 실패한다.
    @property
    def applications(self) -> ApplicationRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
