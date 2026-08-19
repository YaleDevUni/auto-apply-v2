"""Notifier contract test — 승인 요청은 항상 티켓을 발급하고 nonce 는 매번 다르다."""

import pytest

from auto_apply.adapters.clock.system import UuidIdGen
from auto_apply.adapters.notifier.console import ConsoleNotifier
from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, NotifyEvent
from auto_apply.ports.notifier import Notifier


class RecordingNotifier:
    """두 번째 구현 (테스트 대역). 실제 Telegram 어댑터도 이 스위트를 통과해야 한다."""

    def __init__(self) -> None:
        self.requests: list[DecisionRequest] = []
        self.events: list[NotifyEvent] = []
        self._seq = 0
        self._pending_nonce: dict[str, str] = {}

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        self._seq += 1
        self.requests.append(req)
        ticket = DecisionTicket(ticket_id=f"tkt_{self._seq}", nonce=f"nonce_{self._seq}")
        self._pending_nonce[req.application_id] = ticket.nonce
        return ticket

    async def notify(self, event: NotifyEvent) -> None:
        self.events.append(event)

    async def consume_ticket(self, application_id: str, nonce: str) -> bool:
        if self._pending_nonce.get(application_id) != nonce:
            return False
        del self._pending_nonce[application_id]
        return True


@pytest.fixture(params=["console", "recording"])
def notifier(request: pytest.FixtureRequest) -> Notifier:
    if request.param == "console":
        return ConsoleNotifier(UuidIdGen())
    return RecordingNotifier()


def _req(app_id: str = "app_1") -> DecisionRequest:
    return DecisionRequest(
        application_id=app_id,
        workflow_id=f"application-{app_id}",
        title="이력서 승인 요청",
        summary="Wanted / 백엔드 엔지니어",
    )


async def test_request_decision_returns_ticket(notifier: Notifier):
    ticket = await notifier.request_decision(_req())
    assert ticket.ticket_id and ticket.nonce


async def test_nonce_is_unique_per_request(notifier: Notifier):
    a = await notifier.request_decision(_req("app_1"))
    b = await notifier.request_decision(_req("app_2"))
    assert a.nonce != b.nonce, "nonce 가 겹치면 버튼 재사용을 막을 수 없다 (§6)"


async def test_notify_accepts_event(notifier: Notifier):
    await notifier.notify(NotifyEvent(kind="NEEDS_HUMAN", application_id="app_1", message="x"))


async def test_consume_ticket_accepts_matching_nonce_once(notifier: Notifier):
    ticket = await notifier.request_decision(_req("app_1"))
    assert await notifier.consume_ticket("app_1", ticket.nonce) is True
    assert await notifier.consume_ticket("app_1", ticket.nonce) is False, "재사용은 거부돼야 한다"


async def test_consume_ticket_rejects_wrong_nonce(notifier: Notifier):
    await notifier.request_decision(_req("app_1"))
    assert await notifier.consume_ticket("app_1", "guessed-nonce") is False


async def test_consume_ticket_rejects_unknown_application(notifier: Notifier):
    assert await notifier.consume_ticket("never-requested", "anything") is False
