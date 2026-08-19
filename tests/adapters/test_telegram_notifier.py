"""TelegramNotifier — 네트워크 없이 콜백 데이터 형식과 nonce 로직을 검증한다.

nonce 재사용 방지 자체는 tests/ports/test_notifier_contract.py 가 모든 구현에 강제한다.
여기서는 Telegram 고유 표현(callback_data 형식, chat_id 브로드캐스트)만 본다.
"""

from telegram import InlineKeyboardMarkup

from auto_apply.adapters.clock.system import UuidIdGen
from auto_apply.adapters.notifier.telegram import TelegramNotifier
from auto_apply.contracts.dto import DecisionRequest, NotifyEvent


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_message(
        self, chat_id: int, text: str, *, parse_mode: str | None = None, reply_markup: object = None
    ) -> object:
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return object()


def _req() -> DecisionRequest:
    return DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="Wanted / 백엔드 엔지니어 지원 승인",
        summary="https://wanted.co.kr/jobs/1",
        artifact_url="s3://bucket/resume.pdf",
    )


async def test_request_decision_broadcasts_to_all_allowed_chats() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111, 222}), UuidIdGen(), bot=bot)

    await notifier.request_decision(_req())

    assert {m["chat_id"] for m in bot.sent} == {111, 222}


async def test_request_decision_callback_data_encodes_app_id_and_nonce() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    ticket = await notifier.request_decision(_req())

    markup = bot.sent[0]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    approve, reject = markup.inline_keyboard[0]
    assert approve.callback_data == f"a:app_1:{ticket.nonce}"
    assert reject.callback_data == f"r:app_1:{ticket.nonce}"


async def test_notify_sends_plain_message_without_keyboard() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.notify(
        NotifyEvent(kind="NEEDS_HUMAN", application_id="app_1", message="DOM 변경")
    )

    assert "NEEDS_HUMAN" in bot.sent[0]["text"]
    assert "reply_markup" not in bot.sent[0] or bot.sent[0].get("reply_markup") is None


async def test_new_decision_round_invalidates_previous_nonce() -> None:
    """같은 application 에 새 승인 요청이 나가면 이전 nonce 는 더 이상 유효하지 않다."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    first = await notifier.request_decision(_req())
    await notifier.request_decision(_req())

    assert await notifier.consume_ticket("app_1", first.nonce) is False
