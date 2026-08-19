import structlog

from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, NotifyEvent
from auto_apply.ports.clock import IdGen

log = structlog.get_logger(__name__)


class ConsoleNotifier:
    """Telegram 없이 승인 흐름을 개발/테스트하기 위한 어댑터.

    승인 대기 자체는 워크플로우 signal 로 이뤄지므로, 여기서는 사람이 실행할
    signal 명령을 그대로 찍어준다.
    """

    def __init__(self, idgen: IdGen) -> None:
        self._idgen = idgen
        self._pending_nonce: dict[str, str] = {}  # application_id -> nonce (§6)

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        ticket = DecisionTicket(
            ticket_id=self._idgen.new_id("tkt"), nonce=self._idgen.new_id("nonce")
        )
        self._pending_nonce[req.application_id] = ticket.nonce
        log.info(
            "decision.requested",
            application_id=req.application_id,
            workflow_id=req.workflow_id,
            title=req.title,
            artifact_url=req.artifact_url,
            approve_cmd=(
                f"temporal workflow signal --workflow-id {req.workflow_id} "
                f'--name approve --input \'{{"kind":"approve"}}\''
            ),
        )
        return ticket

    async def notify(self, event: NotifyEvent) -> None:
        log.info(
            "notify", kind=event.kind, application_id=event.application_id, message=event.message
        )

    async def consume_ticket(self, application_id: str, nonce: str) -> bool:
        if self._pending_nonce.get(application_id) != nonce:
            return False
        del self._pending_nonce[application_id]
        return True

    def peek_nonce(self, application_id: str) -> str | None:
        """테스트 전용: 발급된 nonce 를 소비하지 않고 들여다본다 (Notifier Protocol 밖)."""
        return self._pending_nonce.get(application_id)
