from typing import Protocol

from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, NotifyEvent


class Notifier(Protocol):
    """'Telegram' 이라는 단어가 없다. 채널 고유 표현은 어댑터 안에 둔다 (§11.6).

    nonce 검증(§6, 오래된 메시지/재전달 방지)은 여기 없다 — `ApplicationWorkflow` 가 발급된
    nonce 를 들고 있다가 signal 로 직접 검증한다. Notifier 어댑터 메모리에 두면 발급
    프로세스(worker)와 검증 프로세스(webhook/listener)가 갈라질 때 항상 실패한다 — 실제로
    라이브 스모크테스트에서 그렇게 터졌다. Temporal 워크플로우는 프로세스 경계와 무관하게
    상태를 들고 있는 유일한 곳이라 거기가 맞는 자리다.
    """

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket: ...

    async def notify(self, event: NotifyEvent) -> None: ...
