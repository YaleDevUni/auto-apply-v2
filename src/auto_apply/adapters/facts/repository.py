from collections.abc import Callable

from auto_apply.contracts.fact import Fact
from auto_apply.domain.experience_facts import experiences_to_facts
from auto_apply.ports.repository import UnitOfWork


class RepositoryFactSource:
    """저장소의 Experience 를 이력서 파이프라인용 Fact 로 펼친다 (§A7).

    UoW 구현(sqlite·memory)이 곧 이 port 의 두 구현이다. 매 호출 새로 읽는다 — 사용자가 경험을
    고치면 바로 다음 생성부터 보여야 한다.
    """

    def __init__(self, uow: Callable[[], UnitOfWork]) -> None:
        self._uow = uow

    async def list_for_user(self, user_id: str) -> list[Fact]:
        async with self._uow() as uow:
            experiences = await uow.experiences.list_for_user(user_id)
        return experiences_to_facts(experiences)
