"""Telegram Bot 어댑터 (ARCHITECTURE.md §6).

채널 고유 표현(인라인 버튼, 마크다운, chat_id)은 여기 안에만 있다 — Notifier port 는
'Telegram' 이라는 단어를 모른다 (§11.6). `bot` 을 주입 가능하게 열어둔 이유는 테스트에서
실제 네트워크 호출 없이 검증하기 위해서다.

nonce 를 여기서 기억하지 않는다 — `ApplicationWorkflow` 가 발급된 nonce 를 들고 있다가
signal 로 직접 검증한다(ports/notifier.py 참고). 이 어댑터는 nonce 를 만들어 버튼에
실어 보내기만 한다.

`store`(BlobStore)를 선택 주입받는 이유 — `DecisionRequest.artifact_url`은 실제로는 PDF 의
블롭 키다(S3 미구현이라 지금은 presigned URL 이 아니라 로컬 파일 키). 사람이 승인 버튼을
누르기 전에 이력서 내용을 실제로 볼 수 있어야 한다는 요구라 텍스트로 키 문자열만 던지는 대신
그 키로 바이트를 읽어 Telegram 문서 첨부로 보낸다. 조회 실패(`BlobNotFound` 등)나 store 가
없으면 조용히 텍스트 전용 메시지로 폴백한다 — 첨부가 승인 자체를 막아선 안 된다.

REVISE(수정요청) 흐름의 scope 선택/자유 텍스트 피드백 요청(`send_scope_picker`/
`send_feedback_prompt`/`send_guide_feedback_prompt`)은 Notifier port 에 없다 — nonce 발급을
동반하는 `request_decision`과 달리 이 셋은 그냥 안내 메시지라 port 표면을 넓힐 필요가 없다.
`telegram/bridge.py`가 이 어댑터 구체 타입으로 직접 부른다(그 모듈도 이미 텔레그램 전용이라
문제 없다).
"""

from typing import Protocol

import structlog

from auto_apply.contracts.dto import DecisionRequest, DecisionTicket, NotifyEvent
from auto_apply.domain.enums import ExecutionMode, RevisionScope
from auto_apply.domain.errors import AutoApplyError
from auto_apply.ports.clock import IdGen
from auto_apply.ports.storage import BlobStore
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

    async def send_document(
        self,
        chat_id: int,
        document: bytes,
        *,
        filename: str,
        caption: str = "",
        reply_markup: InlineKeyboardMarkup | ForceReply | None = None,
    ) -> object: ...

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> object: ...


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_ids: frozenset[int],
        idgen: IdGen,
        *,
        bot: _SendsMessages | None = None,
        store: BlobStore | None = None,
    ) -> None:
        self._bot: _SendsMessages = bot if bot is not None else Bot(token=token)
        self._chat_ids = chat_ids
        self._idgen = idgen
        self._store = store

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        ticket = DecisionTicket(
            ticket_id=self._idgen.new_id("tkt"), nonce=self._idgen.new_id("nonce")
        )
        if req.guide_patch:
            keyboard = _guide_patch_keyboard(req, ticket.nonce)
        elif req.repair_promotion:
            keyboard = _repair_keyboard(req, ticket.nonce)
        else:
            keyboard = _keyboard(req, ticket.nonce)
        # 평문으로 보낸다 — title/summary/artifact_url 은 스크래핑된 공고 데이터라 마크다운
        # 특수문자(_ * ` 등)를 언제든 포함할 수 있다. parse_mode 를 쓰면 그런 문자가 섞일 때마다
        # "can't find end of the entity" 로 전송 자체가 실패한다 (라이브 스모크테스트로 확인).
        # summary 에는 공고 링크(job.url)가 이미 실려 온다(workflows/application.py 참고) —
        # 승인 여부를 판단하려면 원본 공고를 다시 확인할 수 있어야 해서다.
        text = f"{_mode_badge(req)}{req.title}\n{req.summary}"
        pdf_bytes = await self._fetch_pdf(req.artifact_url)
        for chat_id in self._chat_ids:
            if pdf_bytes is not None:
                await self._bot.send_document(
                    chat_id=chat_id,
                    document=pdf_bytes,
                    filename=f"resume_{req.application_id}.pdf",
                    caption=text,
                    reply_markup=keyboard,
                )
            else:
                await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        log.info(
            "telegram.decision_requested",
            application_id=req.application_id,
            guide_patch=req.guide_patch,
            attached_pdf=pdf_bytes is not None,
        )
        return ticket

    async def _fetch_pdf(self, blob_key: str | None) -> bytes | None:
        """승인 버튼을 누르기 전에 이력서 내용을 실제로 볼 수 있어야 한다는 요구.

        조회 실패는 첨부만 포기하고 텍스트 메시지는 그대로 나가야 한다 — 승인 흐름 자체를
        막아서는 안 된다.
        """
        if not blob_key or self._store is None:
            return None
        try:
            return await self._store.get(blob_key)
        except AutoApplyError:
            log.warning("telegram.pdf_attach_failed", blob_key=blob_key)
            return None

    async def notify(self, event: NotifyEvent) -> None:
        text = f"[{event.kind}]"
        if event.application_id:
            text += f" {event.application_id}"
        if event.message:
            text += f"\n{event.message}"
        for chat_id in self._chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=text)

    async def send_scope_picker(self, application_id: str, nonce: str) -> None:
        """REVISE 버튼을 누른 뒤 — 이번 지원에만 반영할지, 앞으로 모든 이력서에 반영할지 고른다.

        취소는 별도 signal 이 필요 없다 — 원래 승인/거절/수정요청 버튼이 실린 메시지의 nonce 는
        이 단계에서 아직 소비되지 않았으므로(수정요청 시작(`v`)도 signal 이 아니다) 취소는
        그냥 "여기서 멈춘다"는 안내만 보내면 된다(telegram/bridge.py 참고).
        """
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "이번만", callback_data=f"vs:{application_id}:specific:{nonce}"
                    ),
                    InlineKeyboardButton(
                        "항상", callback_data=f"vs:{application_id}:general:{nonce}"
                    ),
                    InlineKeyboardButton("취소", callback_data=f"vc:{application_id}:{nonce}"),
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

        ForceReply 와 인라인 버튼은 한 메시지의 `reply_markup`에 동시에 못 실린다(Bot API 제약) —
        그래서 취소 버튼은 별도 메시지로 뒤이어 보낸다. 콜백은 scope 선택 화면의 취소(`vc`)와
        같은 액션을 재사용한다 — 이 단계에서도 원래 승인/거절/수정요청 버튼의 nonce 는 아직
        소비되지 않았으므로 처리(안내만 보내고 원래 버튼으로 계속 진행 가능)가 완전히 같다.
        """
        label = "이번 지원에만" if scope is RevisionScope.SPECIFIC else "앞으로 모든 이력서에"
        text = (
            f"✏️ {label} 반영할 피드백을 입력해 이 메시지에 답장(reply)하세요.\n"
            f"[revise:{application_id}:{nonce}:{scope.value}]"
        )
        markup = ForceReply(selective=True)
        cancel_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("취소", callback_data=f"vc:{application_id}:{nonce}")]]
        )
        cancel_text = "취소하려면 아래 버튼을 누르세요."
        for chat_id in self._chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
            await self._bot.send_message(
                chat_id=chat_id, text=cancel_text, reply_markup=cancel_keyboard
            )

    async def answer_callback_query(self, callback_query_id: str) -> None:
        """버튼을 눌렀을 때 뜨는 "불러오는 중" 스피너를 즉시 꺼준다.

        Telegram 은 `callback_query`마다 `answerCallbackQuery`를 호출해줘야 클라이언트가
        로딩 상태를 해제한다 — 안 부르면 이후 어떤 메시지가 와도 그 스피너는 안 꺼진다
        (버튼을 눌렀는데 계속 로딩만 뜨는 문제, 라이브 스모크테스트로 실측).
        """
        await self._bot.answer_callback_query(callback_query_id)

    async def send_guide_feedback_prompt(self, application_id: str, nonce: str) -> None:
        """가이드 patch 💬 코멘트 버튼을 누른 뒤 — 자유 텍스트 코멘트를 ForceReply 로 받는다.

        `send_feedback_prompt`와 같은 이유로 상태를 안 들고(태그를 메시지 본문에 실어 보내고
        답장에서 복원) 프로세스 경계를 넘나든다. 취소 버튼도 같은 이유로 별도 메시지다 — 다만
        원래 버튼 안내 문구가 REVISE 와 다르므로(반영/무시/코멘트) 액션은 `gc`로 따로 둔다.
        """
        text = (
            "💬 이 가이드 patch 제안에 대한 코멘트를 입력해 이 메시지에 답장(reply)하세요.\n"
            f"[guiderevise:{application_id}:{nonce}]"
        )
        markup = ForceReply(selective=True)
        cancel_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("취소", callback_data=f"gc:{application_id}:{nonce}")]]
        )
        cancel_text = "취소하려면 아래 버튼을 누르세요."
        for chat_id in self._chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
            await self._bot.send_message(
                chat_id=chat_id, text=cancel_text, reply_markup=cancel_keyboard
            )


def _mode_badge(req: DecisionRequest) -> str:
    """실제 제출 여부를 헷갈리지 않도록 메시지 맨 앞에 붙이는 배지 (dry-run-indicator-backlog).

    가이드 patch 승인(`guide_patch=True`)은 실행과 무관해 배지를 안 붙인다. `req.mode`가
    None 인 건 워크플로우가 승인 요청 시점에 recipe 조회에 실패해 못 정했다는 뜻이라(
    `workflows/application.py` `_peek_mode`) "확인 불가"로 명시해 사람이 안심하지 않게 한다.
    """
    if req.guide_patch or req.repair_promotion:
        return ""
    labels = {
        ExecutionMode.DRY_RUN: "🧪 DRY RUN — 실제 제출 안 함",
        ExecutionMode.SUPERVISED: "⚠️ SUPERVISED — 실제 제출(submit 직전 재확인)",
        ExecutionMode.LIVE: "🚨 LIVE — 실제 제출",
    }
    fallback = "❓ 모드 확인 불가 — 승인 전 recipe 상태를 확인하세요"
    label = labels.get(req.mode, fallback) if req.mode is not None else fallback
    return f"{label}\n"


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


def _repair_keyboard(req: DecisionRequest, nonce: str) -> InlineKeyboardMarkup:
    """recipe 승격 승인은 REVISE/코멘트가 없다 — 승격하거나 그대로 candidate 로 둔다.

    `req.application_id`가 `f"{platform}-{form_hash}"`를 담고 있다(§2.4,
    telegram/bridge.py 가 이걸로 `repair-{...}` workflow id 를 복원한다).
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 승격", callback_data=f"pa:{req.application_id}:{nonce}"),
                InlineKeyboardButton("❌ 보류", callback_data=f"pr:{req.application_id}:{nonce}"),
            ]
        ]
    )


def _guide_patch_keyboard(req: DecisionRequest, nonce: str) -> InlineKeyboardMarkup:
    """가이드 patch 승인은 중첩 승인이다 — 💬 코멘트는 본 REVISE 와 달리 scope 선택이 없고

    (가이드 patch 자체가 이미 GENERAL 범위다) `MAX_GUIDE_REVISIONS`로 재제안 횟수만 제한한다
    (workflows/_revision.py 참고).
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 반영", callback_data=f"ga:{req.application_id}:{nonce}"),
                InlineKeyboardButton("❌ 무시", callback_data=f"gr:{req.application_id}:{nonce}"),
                InlineKeyboardButton("💬 코멘트", callback_data=f"gv:{req.application_id}:{nonce}"),
            ]
        ]
    )
