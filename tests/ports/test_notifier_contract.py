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

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        self._seq += 1
        self.requests.append(req)
        return DecisionTicket(ticket_id=f"tkt_{self._seq}", nonce=f"nonce_{self._seq}")

    async def notify(self, event: NotifyEvent) -> None:
        self.events.append(event)


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
