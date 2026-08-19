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

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        ticket = DecisionTicket(
            ticket_id=self._idgen.new_id("tkt"), nonce=self._idgen.new_id("nonce")
        )
        revise_name = "revise_guide_patch" if req.guide_patch else "revise"
        revise_input = (
            '{"feedback":"..."}' if req.guide_patch else '{"feedback":"...","scope":"specific"}'
        )
        revise_cmd = {
            "revise_cmd": (
                f"temporal workflow signal --workflow-id {req.workflow_id} "
                f"--name {revise_name} --input '{revise_input}'"
            )
        }
        log.info(
            "decision.requested",
            application_id=req.application_id,
            workflow_id=req.workflow_id,
            title=req.title,
            artifact_url=req.artifact_url,
            guide_patch=req.guide_patch,
            approve_cmd=(
                f"temporal workflow signal --workflow-id {req.workflow_id} "
                f"--name {'approve_guide_patch' if req.guide_patch else 'approve'} "
                f"--input '{{}}'"
            ),
            **revise_cmd,
        )
        return ticket

    async def notify(self, event: NotifyEvent) -> None:
        log.info(
            "notify", kind=event.kind, application_id=event.application_id, message=event.message
        )
