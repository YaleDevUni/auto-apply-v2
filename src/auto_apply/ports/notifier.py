from typing import Protocol

from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, NotifyEvent


class Notifier(Protocol):
    """'Telegram' 이라는 단어가 없다. 채널 고유 표현은 어댑터 안에 둔다 (§11.6)."""

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket: ...

    async def notify(self, event: NotifyEvent) -> None: ...

    async def consume_ticket(self, application_id: str, nonce: str) -> bool:
        """인바운드 결정(버튼 클릭 등)의 nonce 가 최신 발급분과 일치하면 소비하고 True.

        오래된 메시지 재사용·중복 클릭을 막는다 (§6 approvals.nonce 의 역할).
        한 번 소비되면 같은 nonce 로 다시 호출해도 False — 그래서 "소비"다.
        """
        ...
