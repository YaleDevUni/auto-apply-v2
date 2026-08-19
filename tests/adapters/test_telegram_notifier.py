"""TelegramNotifier — 네트워크 없이 콜백 데이터 형식을 검증한다.

nonce 발급 자체는 tests/ports/test_notifier_contract.py 가 모든 구현에 강제하고, nonce
검증(오래된 메시지 거부)은 워크플로우가 한다 — tests/workflows/test_application.py 와
tests/api/test_telegram_webhook_api.py 참고. 여기서는 Telegram 고유 표현(callback_data
형식, chat_id 브로드캐스트)만 본다.
"""

from auto_apply.adapters.clock.system import UuidIdGen
from auto_apply.adapters.notifier.telegram import TelegramNotifier
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import DecisionRequest, NotifyEvent
from auto_apply.domain.enums import RevisionScope
from telegram import ForceReply, InlineKeyboardMarkup


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.documents: list[dict[str, object]] = []
        self.answered: list[str] = []

    async def send_message(self, chat_id: int, text: str, *, reply_markup: object = None) -> object:
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return object()

    async def send_document(
        self,
        chat_id: int,
        document: bytes,
        *,
        filename: str,
        caption: str = "",
        reply_markup: object = None,
    ) -> object:
        self.documents.append(
            {
                "chat_id": chat_id,
                "document": document,
                "filename": filename,
                "caption": caption,
                "reply_markup": reply_markup,
            }
        )
        return object()

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> object:
        self.answered.append(callback_query_id)
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


async def test_guide_patch_decision_has_approve_reject_and_comment_buttons() -> None:
    """가이드 patch 승인은 중첩 승인이지만 💬 코멘트로 제안 자체를 다시 받을 수 있다."""
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
    assert len(buttons) == 3
    apply_btn, ignore_btn, comment_btn = buttons
    assert apply_btn.callback_data == f"ga:app_1:{ticket.nonce}"
    assert ignore_btn.callback_data == f"gr:app_1:{ticket.nonce}"
    assert comment_btn.callback_data == f"gv:app_1:{ticket.nonce}"


async def test_send_guide_feedback_prompt_uses_force_reply_and_encodes_context() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.send_guide_feedback_prompt("app_1", "nonce_1")

    sent = bot.sent[0]
    assert isinstance(sent["reply_markup"], ForceReply)
    assert "[guiderevise:app_1:nonce_1]" in sent["text"]


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


async def test_answer_callback_query_clears_client_loading_spinner() -> None:
    """회귀 테스트: answerCallbackQuery 를 안 부르면 버튼을 눌렀을 때 뜨는 "불러오는 중"

    스피너가 안 꺼진다 — 실제로 텔레그램 리스너를 재시작 안 하고 새 콜백을 눌렀을 때
    이 문제로 걸렸다(malformed_callback 이었지만, 그 경우에도 스피너는 꺼져야 한다).
    """
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.answer_callback_query("cbq_1")

    assert bot.answered == ["cbq_1"]


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

    assert bot.sent[0]["text"] == ("Fixture Inc. / 백엔드_엔지니어* 지원 승인\n_짝이_안_맞는_밑줄_")


async def test_request_decision_attaches_resume_pdf_when_store_has_it() -> None:
    """PDF 를 텍스트(blob key)로 던지는 대신 실제 바이트를 문서로 첨부한다 — 승인 전에

    이력서 내용을 볼 수 있어야 한다는 요구.
    """
    store = InMemoryBlobStore()
    await store.put("resumes/res_1.pdf", b"%PDF-fake-bytes")
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot, store=store)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="Wanted / 백엔드 엔지니어 지원 승인",
        summary="https://wanted.co.kr/jobs/1",
        artifact_url="resumes/res_1.pdf",
    )

    await notifier.request_decision(req)

    assert bot.sent == []
    doc = bot.documents[0]
    assert doc["document"] == b"%PDF-fake-bytes"
    assert doc["filename"] == "resume_app_1.pdf"
    # 캡션에 공고 링크(summary)가 그대로 실려서 승인 전에 원본 공고를 다시 볼 수 있다.
    assert doc["caption"] == "Wanted / 백엔드 엔지니어 지원 승인\nhttps://wanted.co.kr/jobs/1"
    assert isinstance(doc["reply_markup"], InlineKeyboardMarkup)


async def test_request_decision_falls_back_to_text_when_blob_missing() -> None:
    """조회 실패는 첨부만 포기한다 — 승인 흐름 자체를 막으면 안 된다."""
    store = InMemoryBlobStore()  # 아무것도 put 하지 않음 → get 이 BlobNotFound
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot, store=store)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="Wanted / 백엔드 엔지니어 지원 승인",
        summary="https://wanted.co.kr/jobs/1",
        artifact_url="resumes/missing.pdf",
    )

    await notifier.request_decision(req)

    assert bot.documents == []
    assert bot.sent[0]["text"] == "Wanted / 백엔드 엔지니어 지원 승인\nhttps://wanted.co.kr/jobs/1"
