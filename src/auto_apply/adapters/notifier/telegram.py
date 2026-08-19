"""Telegram Bot 어댑터 (ARCHITECTURE.md §6).

채널 고유 표현(인라인 버튼, 마크다운, chat_id)은 여기 안에만 있다 — Notifier port 는
'Telegram' 이라는 단어를 모른다 (§11.6). `bot` 을 주입 가능하게 열어둔 이유는 테스트에서
실제 네트워크 호출 없이 검증하기 위해서다.

nonce 를 여기서 기억하지 않는다 — `ApplicationWorkflow` 가 발급된 nonce 를 들고 있다가
signal 로 직접 검증한다(ports/notifier.py 참고). 이 어댑터는 nonce 를 만들어 버튼에
실어 보내기만 한다.
"""

from typing import Protocol

import structlog

from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, NotifyEvent
from auto_apply.ports.clock import IdGen
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

log = structlog.get_logger(__name__)


class _SendsMessages(Protocol):
    async def send_message(
        self, chat_id: int, text: str, *, reply_markup: InlineKeyboardMarkup | None = None
    ) -> object: ...


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_ids: frozenset[int],
        idgen: IdGen,
        *,
        bot: _SendsMessages | None = None,
    ) -> None:
        self._bot: _SendsMessages = bot if bot is not None else Bot(token=token)
        self._chat_ids = chat_ids
        self._idgen = idgen

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        ticket = DecisionTicket(
            ticket_id=self._idgen.new_id("tkt"), nonce=self._idgen.new_id("nonce")
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ 승인", callback_data=f"a:{req.application_id}:{ticket.nonce}"
                    ),
                    InlineKeyboardButton(
                        "❌ 거절", callback_data=f"r:{req.application_id}:{ticket.nonce}"
                    ),
                ]
            ]
        )
        # 평문으로 보낸다 — title/summary/artifact_url 은 스크래핑된 공고 데이터라 마크다운
        # 특수문자(_ * ` 등)를 언제든 포함할 수 있다. parse_mode 를 쓰면 그런 문자가 섞일 때마다
        # "can't find end of the entity" 로 전송 자체가 실패한다 (라이브 스모크테스트로 확인).
        text = f"{req.title}\n{req.summary}"
        if req.artifact_url:
            text += f"\n\n{req.artifact_url}"
        for chat_id in self._chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        log.info("telegram.decision_requested", application_id=req.application_id)
        return ticket

    async def notify(self, event: NotifyEvent) -> None:
        text = f"[{event.kind}]"
        if event.application_id:
            text += f" {event.application_id}"
        if event.message:
            text += f"\n{event.message}"
        for chat_id in self._chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=text)
