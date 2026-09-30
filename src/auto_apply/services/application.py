"""지원 건 유스케이스 — 상태를 쓰는 유일한 통로 (§A3, 절대 규칙 6).

저장소의 상태 쓰기(`add`·`append_state`)는 이 모듈만 부른다.
`tests/services/test_state_write_seal.py` 가 src 전체를 훑어 다른 곳의 호출을 막는다.
"""

from collections.abc import Callable
from urllib.parse import urlsplit

import structlog

from auto_apply.contracts.dto import ApplicationRecord, PersistState
from auto_apply.domain.application_state import (
    CRASH_RECOVERY,
    INITIAL_STATE,
    check_transition,
    failure_target,
)
from auto_apply.domain.enums import ApplicationState, SubmitMode
from auto_apply.domain.errors import FailureKind, InvalidInput, InvalidTransition, NotFound
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.repository import UnitOfWork

log = structlog.get_logger(__name__)


def _domain_of(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise InvalidInput("지원 링크는 http(s) 주소여야 한다")
    return parts.hostname.lower()


class ApplicationService:
    def __init__(self, uow: Callable[[], UnitOfWork], clock: Clock, idgen: IdGen) -> None:
        self._uow = uow
        self._clock = clock
        self._idgen = idgen

    async def create(
        self, url: str, *, submit_mode: SubmitMode = SubmitMode.DRY_RUN
    ) -> ApplicationRecord:
        """새 지원 건을 DRAFT 로. 제출 모드는 호출자가 명시하지 않으면 dry_run (절대 규칙 2)."""
        record = ApplicationRecord(
            application_id=self._idgen.new_id("app"),
            url=url,
            domain=_domain_of(url),
            submit_mode=submit_mode,
        )
        initial = PersistState(
            application_id=record.application_id,
            run_id=None,
            state=INITIAL_STATE,
            at=self._clock.now(),
        )
        async with self._uow() as uow:
            await uow.applications.add(record, initial)
            await uow.commit()
        log.info(
            "application.created",
            application_id=record.application_id,
            run_id=None,
            domain=record.domain,
            submit_mode=str(record.submit_mode),
        )
        return record

    async def transition(
        self,
        application_id: str,
        to: ApplicationState,
        *,
        run_id: str | None,
        reason: str = "",
    ) -> PersistState:
        """전이 표(§A3)를 통과한 전이만 쓴다.

        표에 없거나 그 사이 상태가 바뀌었으면 `InvalidTransition` — 아무것도 쓰지 않는다.
        """
        now = self._clock.now()
        async with self._uow() as uow:
            current = (await uow.applications.latest_states([application_id])).get(application_id)
            if current is None:
                raise NotFound(application_id)
            check_transition(current, to)
            state = PersistState(
                application_id=application_id,
                run_id=run_id,
                state=to,
                reason=reason,
                at=now,
                submitted_at=now if to is ApplicationState.SUBMITTED else None,
            )
            # expected=current: 읽은 뒤 다른 쪽(API·러너)이 먼저 바꿨으면 저장소가 거부한다.
            await uow.applications.append_state(state, expected=current)
            await uow.commit()
        log.info(
            "application.transition",
            application_id=application_id,
            run_id=run_id,
            from_state=str(current),
            to_state=str(to),
            reason=reason,
        )
        return state

    async def current_state(self, application_id: str) -> ApplicationState:
        async with self._uow() as uow:
            current = (await uow.applications.latest_states([application_id])).get(application_id)
        if current is None:
            raise NotFound(application_id)
        return current

    async def settle_failure(
        self,
        application_id: str,
        failure: FailureKind,
        *,
        run_id: str | None,
        reason: str,
        retry: bool = False,
    ) -> ApplicationState | None:
        """JobRunner 가 핸들러 실패를 지원 건 상태로 정리한다 (§A9). 바꾼 상태, 안 바꿨으면 None.

        그 사이 다른 쪽(취소 등)이 먼저 바꿨으면 그쪽이 이긴다 — 조용히 None.
        """
        try:
            current = await self.current_state(application_id)
            target = failure_target(current, failure, retry=retry)
            if target is None:
                return None
            await self.transition(application_id, target, run_id=run_id, reason=reason)
        except (InvalidTransition, NotFound):
            return None
        return target

    async def recover_interrupted(self, *, reason: str) -> dict[str, ApplicationState]:
        """끊긴 run 의 지원 건을 직전 재개 가능 상태로 (§A3 크래시 복구). {id: 되돌린 상태}.

        러너가 아무것도 돌리지 않을 때(기동 직후·정지 뒤)만 부른다 — 진행 중 상태가 전부 낡았다.
        """
        async with self._uow() as uow:
            stale = await uow.applications.in_states(CRASH_RECOVERY.keys())
        recovered: dict[str, ApplicationState] = {}
        for application_id, current in stale.items():
            target = CRASH_RECOVERY[current]
            try:
                await self.transition(application_id, target, run_id=None, reason=reason)
            except InvalidTransition:
                continue
            recovered[application_id] = target
        return recovered
