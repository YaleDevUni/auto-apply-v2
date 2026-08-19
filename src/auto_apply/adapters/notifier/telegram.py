"""Telegram Bot 어댑터 (ARCHITECTURE.md §6).

채널 고유 표현(인라인 버튼, 마크다운, chat_id)은 여기 안에만 있다 — Notifier port 는
'Telegram' 이라는 단어를 모른다 (§11.6). `bot` 을 주입 가능하게 열어둔 이유는 테스트에서
실제 네트워크 호출 없이 nonce 로직만 검증하기 위해서다.
"""

from typing import Protocol

import structlog
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, NotifyEvent
from auto_apply.ports.clock import IdGen

log = structlog.get_logger(__name__)


class _SendsMessages(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
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
        self._pending_nonce: dict[str, str] = {}  # application_id -> nonce (§6)

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        ticket = DecisionTicket(
            ticket_id=self._idgen.new_id("tkt"), nonce=self._idgen.new_id("nonce")
        )
        self._pending_nonce[req.application_id] = ticket.nonce
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
        text = f"*{req.title}*\n{req.summary}"
        if req.artifact_url:
            text += f"\n\n{req.artifact_url}"
        for chat_id in self._chat_ids:
            await self._bot.send_message(
                chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
            )
        log.info("telegram.decision_requested", application_id=req.application_id)
        return ticket

    async def notify(self, event: NotifyEvent) -> None:
        text = f"*{event.kind}*"
        if event.application_id:
            text += f" · `{event.application_id}`"
        if event.message:
            text += f"\n{event.message}"
        for chat_id in self._chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)

    async def consume_ticket(self, application_id: str, nonce: str) -> bool:
        if self._pending_nonce.get(application_id) != nonce:
            return False
        del self._pending_nonce[application_id]
        return True
