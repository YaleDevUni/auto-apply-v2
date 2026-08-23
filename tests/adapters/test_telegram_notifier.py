"""TelegramNotifier — 네트워크 없이 콜백 데이터 형식을 검증한다.

nonce 발급 자체는 tests/ports/test_notifier_contract.py 가 모든 구현에 강제하고, nonce
검증(오래된 메시지 거부)은 워크플로우가 한다 — tests/workflows/test_application.py 와
tests/api/test_telegram_webhook_api.py 참고. 여기서는 Telegram 고유 표현(callback_data
형식, chat_id 브로드캐스트)만 본다.
"""

import pytest
from telegram.error import BadRequest

from auto_apply.adapters.clock.system import UuidIdGen
from auto_apply.adapters.notifier.telegram import TelegramNotifier
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.dto import DecisionRequest, NotifyEvent
from auto_apply.domain.enums import ExecutionMode, RevisionScope
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


async def test_scope_picker_callback_data_fits_telegram_limit() -> None:
    """실측 회귀: application_id 30자(예: CLI `start` 로 사람이 고른 id) + nonce 조합이

    "vs:{id}:specific:{nonce}" 에서 65바이트로 Telegram 의 64바이트 callback_data 한계를
    넘어 BUTTON_DATA_INVALID 로 메시지 전송 자체가 실패했다(라이브에서 실측). 이후
    `StartApplication.application_id` 에 24자 상한을 걸었다(contracts/dto.py) — 그 상한에서
    가장 빠듯한 버튼도 64바이트를 넘지 않는지 여기서 고정한다.
    """
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    max_len_app_id = "a" * 24  # StartApplication.application_id 의 상한과 일치시킨다

    ticket = await notifier.request_decision(
        DecisionRequest(
            application_id=max_len_app_id,
            workflow_id=f"application-{max_len_app_id}",
            title="t",
            summary="s",
        )
    )
    await notifier.send_scope_picker(max_len_app_id, ticket.nonce)

    markup = bot.sent[-1]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    for button in markup.inline_keyboard[0]:
        assert len(button.callback_data.encode()) <= 64, button.callback_data


async def test_request_decision_prefixes_dry_run_badge() -> None:
    """dry-run-indicator-backlog: dry-run 인지 실제 제출인지 메시지만 보고 구분할 수 있어야 한다."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="Wanted / 백엔드 엔지니어 지원 승인",
        summary="https://wanted.co.kr/jobs/1",
        mode=ExecutionMode.DRY_RUN,
    )

    await notifier.request_decision(req)

    assert bot.sent[0]["text"].startswith("🧪 DRY RUN")


async def test_request_decision_prefixes_live_badge() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="Wanted / 백엔드 엔지니어 지원 승인",
        summary="https://wanted.co.kr/jobs/1",
        mode=ExecutionMode.LIVE,
    )

    await notifier.request_decision(req)

    assert bot.sent[0]["text"].startswith("🚨 LIVE")


async def test_request_decision_prefixes_unknown_badge_when_mode_missing() -> None:
    """recipe 조회 실패로 워크플로우가 mode 를 못 정했을 때(None) 안심시키지 않는다."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = _req()  # mode 미지정 → None

    await notifier.request_decision(req)

    assert bot.sent[0]["text"].startswith("❓ 모드 확인 불가")


async def test_request_decision_shows_caution_documents_portfolio_and_notes() -> None:
    """wanted-application-caution-indicators-backlog: 승인 전 정보 비대칭 해소 배지 셋."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="Wanted / 백엔드 엔지니어 지원 승인",
        summary="https://wanted.co.kr/jobs/1",
        mode=ExecutionMode.DRY_RUN,
        caution_documents=["성적증명서", "경력증명서"],
        portfolio_filename="박예일_포트폴리오.pdf",
        caution_notes=["경력 요건 대비 근거가 빠듯함"],
    )

    await notifier.request_decision(req)

    text = bot.sent[0]["text"]
    assert "📎 준비 필요 서류: 성적증명서, 경력증명서" in text
    assert "🗂 첨부 포트폴리오: 박예일_포트폴리오.pdf" in text
    assert "⚠️ 경력 요건 대비 근거가 빠듯함" in text


async def test_request_decision_omits_caution_section_when_empty() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.request_decision(_req())

    text = bot.sent[0]["text"]
    assert "📎" not in text
    assert "🗂" not in text
    assert "⚠️" not in text


async def test_guide_patch_decision_has_no_caution_section() -> None:
    """중첩 승인(가이드 patch)은 실행과 무관해 caution 배지도 안 붙는다."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="가이드 수정 제안",
        summary="diff",
        guide_patch=True,
        caution_documents=["성적증명서"],
        portfolio_filename="x.pdf",
        caution_notes=["note"],
    )

    await notifier.request_decision(req)

    text = bot.sent[0]["text"]
    assert "📎" not in text
    assert "🗂" not in text
    assert "⚠️" not in text


async def test_guide_patch_decision_has_no_mode_badge() -> None:
    """가이드 patch 승인은 실행과 무관해 배지를 안 붙인다."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="이력서 가이드 수정 제안",
        summary="- 기존: (없음)\n+ 변경: 항상 존댓말로 쓴다.",
        guide_patch=True,
    )

    await notifier.request_decision(req)

    assert bot.sent[0]["text"] == (
        "이력서 가이드 수정 제안\n- 기존: (없음)\n+ 변경: 항상 존댓말로 쓴다."
    )


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


async def test_repair_promotion_decision_has_no_mode_badge() -> None:
    """recipe 승격 승인(§2.4)도 실행 모드와 무관해 배지를 안 붙인다."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="fixture-h-fixture-1",
        workflow_id="repair-fixture-h-fixture-1",
        title="fixture recipe v2 승격 승인",
        summary="actions 3개, success_signals=['완료']",
        repair_promotion=True,
    )

    await notifier.request_decision(req)

    assert bot.sent[0]["text"] == (
        "fixture recipe v2 승격 승인\nactions 3개, success_signals=['완료']"
    )


async def test_repair_promotion_decision_has_only_approve_and_hold_buttons() -> None:
    """REVISE/코멘트가 없다 — 승격하거나 그대로 candidate 로 둔다."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="fixture-h-fixture-1",
        workflow_id="repair-fixture-h-fixture-1",
        title="fixture recipe v2 승격 승인",
        summary="actions 3개",
        repair_promotion=True,
    )

    ticket = await notifier.request_decision(req)

    markup = bot.sent[0]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    buttons = markup.inline_keyboard[0]
    assert len(buttons) == 2
    promote_btn, hold_btn = buttons
    assert promote_btn.callback_data == f"pa:fixture-h-fixture-1:{ticket.nonce}"
    assert hold_btn.callback_data == f"pr:fixture-h-fixture-1:{ticket.nonce}"


async def test_checkpoint_decision_has_no_mode_badge() -> None:
    """체크포인트 승인도 실행 모드와 무관해 배지를 안 붙인다 — 이미 SUPERVISED 도중임이 자명하다."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="app_1 — 00:submit 체크포인트 승인",
        summary="attempt 1: 다음 단계로 진행하려면 승인하세요.",
        checkpoint=True,
    )

    await notifier.request_decision(req)

    assert bot.sent[0]["text"] == (
        "app_1 — 00:submit 체크포인트 승인\nattempt 1: 다음 단계로 진행하려면 승인하세요."
    )


async def test_checkpoint_decision_has_only_continue_and_stop_buttons() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="app_1 — 00:submit 체크포인트 승인",
        summary="attempt 1",
        checkpoint=True,
    )

    ticket = await notifier.request_decision(req)

    markup = bot.sent[0]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    buttons = markup.inline_keyboard[0]
    assert len(buttons) == 2
    continue_btn, stop_btn = buttons
    assert continue_btn.callback_data == f"ca:app_1:{ticket.nonce}"
    assert stop_btn.callback_data == f"cr:app_1:{ticket.nonce}"


async def test_checkpoint_decision_attaches_screenshot_with_checkpoint_filename() -> None:
    store = InMemoryBlobStore()
    await store.put("checkpoints/app_1/1/00.png", b"fake-png-bytes")
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot, store=store)
    req = DecisionRequest(
        application_id="app_1",
        workflow_id="application-app_1",
        title="체크포인트 승인",
        summary="attempt 1",
        artifact_url="checkpoints/app_1/1/00.png",
        checkpoint=True,
    )

    await notifier.request_decision(req)

    assert bot.sent == []
    doc = bot.documents[0]
    assert doc["document"] == b"fake-png-bytes"
    assert doc["filename"] == "checkpoint_app_1.png"


async def test_send_guide_feedback_prompt_uses_force_reply_and_encodes_context() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.send_guide_feedback_prompt("app_1", "nonce_1")

    sent = bot.sent[0]
    assert isinstance(sent["reply_markup"], ForceReply)
    assert "[guiderevise:app_1:nonce_1]" in sent["text"]


async def test_send_guide_feedback_prompt_also_sends_cancel_button() -> None:
    """ForceReply 는 인라인 버튼과 한 메시지에 못 실리므로 취소 버튼은 뒤이은 메시지다."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.send_guide_feedback_prompt("app_1", "nonce_1")

    cancel_msg = bot.sent[1]
    markup = cancel_msg["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    (cancel_btn,) = markup.inline_keyboard[0]
    assert cancel_btn.callback_data == "gc:app_1:nonce_1"


async def test_send_scope_picker_encodes_specific_general_and_cancel_options() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.send_scope_picker("app_1", "nonce_1")

    markup = bot.sent[0]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    specific, general, cancel = markup.inline_keyboard[0]
    assert specific.callback_data == "vs:app_1:specific:nonce_1"
    assert general.callback_data == "vs:app_1:general:nonce_1"
    assert cancel.callback_data == "vc:app_1:nonce_1"


async def test_send_feedback_prompt_uses_force_reply_and_encodes_context() -> None:
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.send_feedback_prompt("app_1", "nonce_1", RevisionScope.GENERAL)

    sent = bot.sent[0]
    assert isinstance(sent["reply_markup"], ForceReply)
    assert "[revise:app_1:nonce_1:general]" in sent["text"]


async def test_send_feedback_prompt_also_sends_cancel_button() -> None:
    """ForceReply 는 인라인 버튼과 한 메시지에 못 실리므로 취소 버튼은 뒤이은 메시지다.

    콜백은 scope 선택 화면의 취소(`vc`)와 같은 액션을 재사용한다 — 원래 승인/거절/수정요청
    버튼의 nonce 가 이 단계에서도 아직 안 쓰였으므로 처리가 완전히 같다.
    """
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.send_feedback_prompt("app_1", "nonce_1", RevisionScope.SPECIFIC)

    cancel_msg = bot.sent[1]
    markup = cancel_msg["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    (cancel_btn,) = markup.inline_keyboard[0]
    assert cancel_btn.callback_data == "vc:app_1:nonce_1"


async def test_answer_callback_query_clears_client_loading_spinner() -> None:
    """회귀 테스트: answerCallbackQuery 를 안 부르면 버튼을 눌렀을 때 뜨는 "불러오는 중"

    스피너가 안 꺼진다 — 실제로 텔레그램 리스너를 재시작 안 하고 새 콜백을 눌렀을 때
    이 문제로 걸렸다(malformed_callback 이었지만, 그 경우에도 스피너는 꺼져야 한다).
    """
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=bot)

    await notifier.answer_callback_query("cbq_1")

    assert bot.answered == ["cbq_1"]


async def test_answer_callback_query_swallows_stale_query_error() -> None:
    """회귀 테스트: 리스너가 꺼져 있던 동안 눌린 버튼은 재기동 후 `BadRequest("Query is too

    old...")`를 던진다(라이브 실측) — 실패가 아니라 정상 상황이라 삼켜야
    `handle_callback_query`(bridge.py)가 뒤이은 실제 승인/거절 처리를 계속 진행한다.
    """

    class _StaleBot(FakeBot):
        async def answer_callback_query(
            self, callback_query_id: str, text: str | None = None
        ) -> object:
            raise BadRequest("Query is too old and response timeout expired or query id is invalid")

    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=_StaleBot())

    await notifier.answer_callback_query("cbq_1")  # 예외 없이 끝나면 충분하다


async def test_answer_callback_query_reraises_unknown_bad_request() -> None:
    """모르는 `BadRequest`는 삼키지 않는다 — 원래 알림 경로로 사람에게 보여야 한다."""

    class _BrokenBot(FakeBot):
        async def answer_callback_query(
            self, callback_query_id: str, text: str | None = None
        ) -> object:
            raise BadRequest("Chat not found")

    notifier = TelegramNotifier("token", frozenset({111}), UuidIdGen(), bot=_BrokenBot())

    with pytest.raises(BadRequest):
        await notifier.answer_callback_query("cbq_1")


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
        mode=ExecutionMode.DRY_RUN,
    )

    await notifier.request_decision(req)

    assert bot.sent[0]["text"] == (
        "🧪 DRY RUN — 실제 제출 안 함\n"
        "Fixture Inc. / 백엔드_엔지니어* 지원 승인\n_짝이_안_맞는_밑줄_"
    )


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
        mode=ExecutionMode.DRY_RUN,
    )

    await notifier.request_decision(req)

    assert bot.sent == []
    doc = bot.documents[0]
    assert doc["document"] == b"%PDF-fake-bytes"
    assert doc["filename"] == "resume_app_1.pdf"
    # 캡션에 공고 링크(summary)가 그대로 실려서 승인 전에 원본 공고를 다시 볼 수 있다.
    assert doc["caption"] == (
        "🧪 DRY RUN — 실제 제출 안 함\nWanted / 백엔드 엔지니어 지원 승인\nhttps://wanted.co.kr/jobs/1"
    )
    assert isinstance(doc["reply_markup"], InlineKeyboardMarkup)


async def test_resend_decision_reuses_the_given_nonce_with_original_buttons() -> None:
    """resend_decision 은 새 nonce 를 만들지 않는다 — 원래 승인/거절/수정요청 버튼과 같은 콜백."""
    bot = FakeBot()
    notifier = TelegramNotifier("token", frozenset({111, 222}), UuidIdGen(), bot=bot)

    await notifier.resend_decision("app_1", "nonce_1")

    assert {m["chat_id"] for m in bot.sent} == {111, 222}
    markup = bot.sent[0]["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    approve, reject, revise = markup.inline_keyboard[0]
    assert approve.callback_data == "a:app_1:nonce_1"
    assert reject.callback_data == "r:app_1:nonce_1"
    assert revise.callback_data == "v:app_1:nonce_1"


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
        mode=ExecutionMode.DRY_RUN,
    )

    await notifier.request_decision(req)

    assert bot.documents == []
    assert bot.sent[0]["text"] == (
        "🧪 DRY RUN — 실제 제출 안 함\nWanted / 백엔드 엔지니어 지원 승인\nhttps://wanted.co.kr/jobs/1"
    )
