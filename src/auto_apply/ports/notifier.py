from typing import Protocol

from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, NotifyEvent


class Notifier(Protocol):
    """'Telegram' 이라는 단어가 없다. 채널 고유 표현은 어댑터 안에 둔다 (§11.6)."""

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket: ...

    async def notify(self, event: NotifyEvent) -> None: ...
