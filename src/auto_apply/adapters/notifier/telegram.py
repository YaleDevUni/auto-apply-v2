"""Telegram Bot 어댑터 (ARCHITECTURE.md §6).

채널 고유 표현(인라인 버튼, 마크다운, chat_id)은 여기 안에만 있다 — Notifier port 는
'Telegram' 이라는 단어를 모른다 (§11.6). `bot` 을 주입 가능하게 열어둔 이유는 테스트에서
실제 네트워크 호출 없이 검증하기 위해서다.

nonce 를 여기서 기억하지 않는다 — `ApplicationWorkflow` 가 발급된 nonce 를 들고 있다가
signal 로 직접 검증한다(ports/notifier.py 참고). 이 어댑터는 nonce 를 만들어 버튼에
실어 보내기만 한다.

REVISE(수정요청) 흐름의 scope 선택/자유 텍스트 피드백 요청(`send_scope_picker`/
`send_feedback_prompt`)은 Notifier port 에 없다 — nonce 발급을 동반하는 `request_decision`과
달리 이 둘은 그냥 안내 메시지라 port 표면을 넓힐 필요가 없다. `telegram/bridge.py`가 이
어댑터 구체 타입으로 직접 부른다(그 모듈도 이미 텔레그램 전용이라 문제 없다).
"""

from typing import Protocol

import structlog

from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, NotifyEvent
from auto_apply.domain.enums import RevisionScope
from auto_apply.ports.clock import IdGen
from telegram import Bot, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

log = structlog.get_logger(__name__)


class _SendsMessages(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | ForceReply | None = None,
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
        keyboard = (
            _guide_patch_keyboard(req, ticket.nonce)
            if req.guide_patch
            else _keyboard(req, ticket.nonce)
        )
        # 평문으로 보낸다 — title/summary/artifact_url 은 스크래핑된 공고 데이터라 마크다운
        # 특수문자(_ * ` 등)를 언제든 포함할 수 있다. parse_mode 를 쓰면 그런 문자가 섞일 때마다
        # "can't find end of the entity" 로 전송 자체가 실패한다 (라이브 스모크테스트로 확인).
        text = f"{req.title}\n{req.summary}"
        if req.artifact_url:
            text += f"\n\n{req.artifact_url}"
        for chat_id in self._chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        log.info(
            "telegram.decision_requested",
            application_id=req.application_id,
            guide_patch=req.guide_patch,
        )
        return ticket

    async def notify(self, event: NotifyEvent) -> None:
        text = f"[{event.kind}]"
        if event.application_id:
            text += f" {event.application_id}"
        if event.message:
            text += f"\n{event.message}"
        for chat_id in self._chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=text)

    async def send_scope_picker(self, application_id: str, nonce: str) -> None:
        """REVISE 버튼을 누른 뒤 — 이번 지원에만 반영할지, 앞으로 모든 이력서에 반영할지 고른다."""
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "이번만", callback_data=f"vs:{application_id}:specific:{nonce}"
                    ),
                    InlineKeyboardButton(
                        "항상", callback_data=f"vs:{application_id}:general:{nonce}"
                    ),
                ]
            ]
        )
        text = "수정 피드백을 이번 지원에만 반영할까요, 앞으로 모든 이력서에 반영할까요?"
        for chat_id in self._chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)

    async def send_feedback_prompt(
        self, application_id: str, nonce: str, scope: RevisionScope
    ) -> None:
        """scope 선택 뒤 — 자유 텍스트 피드백을 ForceReply 로 받는다.

        어떤 application/nonce/scope 인지는 이 메시지 본문에 그대로 실어 보낸다 — 사용자가
        이 메시지에 답장(reply)하면 Telegram 이 `reply_to_message`로 원문을 그대로 되돌려주므로,
        webhook/리스너 프로세스가 별도 상태를 들고 있지 않아도(§6과 같은 이유) 텍스트만 보고
        어느 요청에 대한 답인지 복원할 수 있다.
        """
        label = "이번 지원에만" if scope is RevisionScope.SPECIFIC else "앞으로 모든 이력서에"
        text = (
            f"✏️ {label} 반영할 피드백을 입력해 이 메시지에 답장(reply)하세요.\n"
            f"[revise:{application_id}:{nonce}:{scope.value}]"
        )
        markup = ForceReply(selective=True)
        for chat_id in self._chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)


def _keyboard(req: DecisionRequest, nonce: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 승인", callback_data=f"a:{req.application_id}:{nonce}"),
                InlineKeyboardButton("❌ 거절", callback_data=f"r:{req.application_id}:{nonce}"),
                InlineKeyboardButton("✏️ 수정요청", callback_data=f"v:{req.application_id}:{nonce}"),
            ]
        ]
    )


def _guide_patch_keyboard(req: DecisionRequest, nonce: str) -> InlineKeyboardMarkup:
    """가이드 patch 승인은 중첩 승인이라 REVISE 버튼이 없다 — 그 자체를 다시 REVISE 할 순 없다."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 반영", callback_data=f"ga:{req.application_id}:{nonce}"),
                InlineKeyboardButton("❌ 무시", callback_data=f"gr:{req.application_id}:{nonce}"),
            ]
        ]
    )
