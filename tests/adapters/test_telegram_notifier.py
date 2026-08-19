"""TelegramNotifier — 네트워크 없이 콜백 데이터 형식을 검증한다.

nonce 발급 자체는 tests/ports/test_notifier_contract.py 가 모든 구현에 강제하고, nonce
검증(오래된 메시지 거부)은 워크플로우가 한다 — tests/workflows/test_application.py 와
tests/api/test_telegram_webhook_api.py 참고. 여기서는 Telegram 고유 표현(callback_data
형식, chat_id 브로드캐스트)만 본다.
"""

from auto_apply.adapters.clock.system import UuidIdGen
from auto_apply.adapters.notifier.telegram import TelegramNotifier
from auto_apply.contracts.dto import DecisionRequest, NotifyEvent
from auto_apply.domain.enums import RevisionScope
from telegram import ForceReply, InlineKeyboardMarkup


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_message(self, chat_id: int, text: str, *, reply_markup: object = None) -> object:
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
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
    approve, reject, revise = markup.inline_keyboard[0]
    assert approve.callback_data == f"a:app_1:{ticket.nonce}"
    assert reject.callback_data == f"r:app_1:{ticket.nonce}"
    assert revise.callback_data == f"v:app_1:{ticket.nonce}"


async def test_guide_patch_decision_has_only_approve_reject_no_revise() -> None:
    """가이드 patch 승인은 중첩 승인이라 REVISE 버튼이 없다."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="이력서 가이드 수정 제안",
        summary="- 기존: (없음)\n+ 변경: 항상 존댓말로 쓴다.",
        guide_patch=True,
    )

    ticket = await notifier.request_decision(req)

    markup = bot.sent[0]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    buttons = markup.inline_keyboard[0]
    assert len(buttons) == 2
    apply_btn, ignore_btn = buttons
    assert apply_btn.callback_data == f"ga:app_1:{ticket.nonce}"
    assert ignore_btn.callback_data == f"gr:app_1:{ticket.nonce}"


async def test_send_scope_picker_encodes_specific_and_general_options() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.send_scope_picker("app_1", "nonce_1")

    markup = bot.sent[0]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    specific, general = markup.inline_keyboard[0]
    assert specific.callback_data == "vs:app_1:specific:nonce_1"
    assert general.callback_data == "vs:app_1:general:nonce_1"


async def test_send_feedback_prompt_uses_force_reply_and_encodes_context() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.send_feedback_prompt("app_1", "nonce_1", RevisionScope.GENERAL)

    sent = bot.sent[0]
    assert isinstance(sent["reply_markup"], ForceReply)
    assert "[revise:app_1:nonce_1:general]" in sent["text"]


async def test_notify_sends_plain_message_without_keyboard() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.notify(
        NotifyEvent(kind="NEEDS_HUMAN", application_id="app_1", message="DOM 변경")
    )

    assert "NEEDS_HUMAN" in bot.sent[0]["text"]
    assert "reply_markup" not in bot.sent[0] or bot.sent[0].get("reply_markup") is None


async def test_request_decision_passes_through_markdown_special_chars() -> None:
    """회귀 테스트: 실제 라이브 스모크테스트에서 blob key 의 `_` 때문에 legacy Markdown

    parse_mode 가 "can't find end of the entity" 로 전송 자체를 실패시켰다. 이제 평문으로
    보내므로 어떤 특수문자가 섞여도 send_message 호출 자체는 그대로 나가야 한다.
    """
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="Fixture Inc. / 백엔드_엔지니어* 지원 승인",
        summary="_짝이_안_맞는_밑줄_",
        artifact_url="resumes/res_293033665fef4ddd.json",
    )

    await notifier.request_decision(req)

    assert bot.sent[0]["text"] == (
        "Fixture Inc. / 백엔드_엔지니어* 지원 승인\n"
        "_짝이_안_맞는_밑줄_\n\n"
        "resumes/res_293033665fef4ddd.json"
    )
