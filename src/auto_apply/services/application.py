"""지원 건 유스케이스 — 상태를 쓰는 유일한 통로 (§A3, 절대 규칙 6).

저장소의 상태 쓰기(`add`·`append_state`)는 이 모듈만 부른다.
`tests/services/test_state_write_seal.py` 가 src 전체를 훑어 다른 곳의 호출을 막는다.
"""

from collections.abc import Callable
from urllib.parse import urlsplit

import structlog

from auto_apply.contracts.dto import ApplicationRecord, PersistState
from auto_apply.domain.application_state import INITIAL_STATE, check_transition
from auto_apply.domain.enums import ApplicationState, SubmitMode
from auto_apply.domain.errors import InvalidInput, NotFound
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
