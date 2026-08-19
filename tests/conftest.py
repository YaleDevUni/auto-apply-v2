"""테스트 공용 빌더.

워크플로우 테스트는 bootstrap 을 쓰지 않고 필요한 어댑터만 직접 조립한다.
그래야 "이 테스트가 무엇을 대역으로 쓰는지" 가 테스트 안에서 드러난다.
"""

from dataclasses import dataclass, field

from auto_apply.activities.application import ApplicationActivities
from auto_apply.activities.browser import BrowserActivities
from auto_apply.activities.guide import GuideActivities
from auto_apply.activities.resume import ResumeActivities
from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.executor.replay import ReplayExecutor
from auto_apply.adapters.facts.static import StaticFactSource
from auto_apply.adapters.guide.static import StaticGuideSource
from auto_apply.adapters.job_source.fixture import FixtureJobSource
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.matching_config.static import StaticMatchingConfigSource
from auto_apply.adapters.notifier.console import ConsoleNotifier
from auto_apply.adapters.notifier.telegram import TelegramNotifier
from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.platform.fixture import FixturePlatformAdapter
from auto_apply.adapters.platform.registry import StaticPlatformRegistry
from auto_apply.adapters.profile.static import StaticProfileSource
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.bootstrap import Container
from auto_apply.config import Settings
from auto_apply.contracts.dto import (
    ApplicationAttempt,
    DecisionRequest,
    DecisionTicket,
    NotifyEvent,
    PersistState,
)
from auto_apply.contracts.fact import Fact
from auto_apply.contracts.profile import Profile
from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.domain.enums import RevisionScope
from auto_apply.ports.notifier import Notifier

JOB_URL = "https://fixture.local/jobs/1"

# ResumeWorkflow 는 MAX_REVIEW_ROUNDS(3)까지 재시도할 수 있다 — 넉넉히 반복해서 등록해둔다.
_RESUME_PAYLOADS = [
    {
        "summary": "공고 요건에 맞춘 경력 요약입니다",
        "highlights": [{"text": "결제 API 개발 경험", "fact_ids": ["exp-fixture-1"]}],
    }
] * 20


def _sample_facts(user_id: str = "u1") -> list[Fact]:
    return [
        Fact(
            id="exp-fixture-1",
            user_id=user_id,
            kind="experience",
            content="테스트용 경력 사실",
            keywords=["백엔드"],
        )
    ]


def _sample_profile(user_id: str = "u1") -> StaticProfileSource:
    return StaticProfileSource([Profile(user_id=user_id, name="테스트 사용자")])


class _FakeBot:
    """실제 네트워크 호출 없이 TelegramNotifier 를 돌리기 위한 대역 (adapters/notifier/telegram.py

    의 `_SendsMessages` 만 만족하면 된다). test_telegram_notifier.py 의 FakeBot 과 같은 모양.
    """

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


@dataclass
class _NonceSpy:
    """테스트 전용 관찰자 — 실제 nonce 검증은 워크플로우가 한다(ports/notifier.py 참고).

    웹훅/리스너 콜백을 흉내내려면 방금 발급된 nonce 를 알아야 하는데, 그건 이제 워크플로우
    안에만 있어서 밖에서 조회할 방법이 없다. 그래서 여기서 발급 시점에 옆에서 훔쳐본다.
    REVISE 텔레그램 흐름(scope 선택/ForceReply) 테스트는 `inner`가 `TelegramNotifier`일 때만
    `send_scope_picker`/`send_feedback_prompt`를 그대로 위임한다 — telegram/bridge.py 가
    `_RevisableNotifier`(구조적 Protocol)로 이 메서드들을 부른다.
    """

    inner: Notifier
    last_ticket: dict[str, str] = field(default_factory=dict)
    # NEEDS_HUMAN/EXPIRED 종료 시 실제로 notify 가 나가는지(라이브 세션에서 실측된 회귀,
    # workflows/application.py `_finish` 참고) 관찰하기 위한 로그.
    notified: list[NotifyEvent] = field(default_factory=list)

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        ticket = await self.inner.request_decision(req)
        self.last_ticket[req.application_id] = ticket.nonce
        return ticket

    async def notify(self, event: NotifyEvent) -> None:
        self.notified.append(event)
        await self.inner.notify(event)

    async def send_scope_picker(self, application_id: str, nonce: str) -> None:
        assert isinstance(self.inner, TelegramNotifier)
        await self.inner.send_scope_picker(application_id, nonce)

    async def send_feedback_prompt(
        self, application_id: str, nonce: str, scope: RevisionScope
    ) -> None:
        assert isinstance(self.inner, TelegramNotifier)
        await self.inner.send_feedback_prompt(application_id, nonce, scope)

    async def send_guide_feedback_prompt(self, application_id: str, nonce: str) -> None:
        assert isinstance(self.inner, TelegramNotifier)
        await self.inner.send_guide_feedback_prompt(application_id, nonce)

    async def answer_callback_query(self, callback_query_id: str) -> None:
        assert isinstance(self.inner, TelegramNotifier)
        await self.inner.answer_callback_query(callback_query_id)


def sample_recipe(
    *, status: str = "active", with_submit: bool = True, submit_selector: str = "#submit"
) -> AutomationRecipe:
    actions = [
        Action(type=ActionType.GOTO, value_literal=JOB_URL),
        Action(type=ActionType.FILL, selector="#email", value_ref="profile.email"),
        Action(type=ActionType.ASSERT_VISIBLE, selector="#form"),
    ]
    if with_submit:
        actions.append(Action(type=ActionType.SUBMIT, selector=submit_selector))
    return AutomationRecipe(
        platform="fixture",
        version=1,
        status=status,  # type: ignore[arg-type]
        form_hash="h-fixture-1",
        actions=actions,
        success_signals=["지원이 완료되었습니다"],
    )


@dataclass
class Harness:
    """워크플로우 테스트용 조립체. rows 로 DB projection 을 검사한다."""

    rows: dict[str, list[PersistState]] = field(default_factory=dict)
    attempt_rows: dict[str, list[ApplicationAttempt]] = field(default_factory=dict)
    eligible: bool = True
    reject_reason: str = ""
    verified: bool = True
    fail_selectors: frozenset[str] = frozenset()
    recipe_status: str = "active"
    # activities() 와 container() 가 같은 인스턴스를 써야 GET 이 workers 쪽 persist 결과를
    # 그대로 읽는다(rows 공유) — nonce 검증 자체는 더 이상 여기 있지 않다(워크플로우가 한다).
    notifier: _NonceSpy | None = None
    # 마찬가지로 activities() 와 container() 가 공유해야 한다 — REVISE(general) 테스트가
    # apply_guide_patch(worker 쪽)로 바뀐 내용을 이 인스턴스로 확인한다.
    guide: StaticGuideSource | None = None
    # StubLLM 이 GuidePatchSchema 요청에도 순서대로 payload 를 내주므로, guide patch 를 쓰는
    # 테스트는 여기 채워서 다음 propose_guide_patch 호출이 이 값을 쓰게 한다.
    guide_patch_payloads: list[dict[str, object]] = field(default_factory=list)

    def _shared_notifier(self, *, telegram: bool = False) -> _NonceSpy:
        """첫 호출이 종류를 정한다(이후는 메모이즈) — REVISE 텔레그램 흐름 테스트는

        `container(settings=Settings(notifier="telegram"))`를 `_Workers(...)`보다 먼저 호출해서
        (그래야 activities() 의 기본 호출보다 먼저 이 분기를 탄다) telegram=True 로 결정한다.
        """
        if self.notifier is None:
            inner = (
                TelegramNotifier("test-token", frozenset({42}), UuidIdGen(), bot=_FakeBot())
                if telegram
                else ConsoleNotifier(UuidIdGen())
            )
            self.notifier = _NonceSpy(inner)
        return self.notifier

    def _shared_guide(self) -> StaticGuideSource:
        if self.guide is None:
            self.guide = StaticGuideSource()
        return self.guide

    def activities(self) -> list[object]:
        idgen = UuidIdGen()
        clock = SystemClock()
        store = InMemoryBlobStore()
        adapter = FixturePlatformAdapter(
            eligible=self.eligible,
            reject_reason=self.reject_reason,
            verified=self.verified,
        )
        recipes = InMemoryRecipeSource({"fixture": sample_recipe(status=self.recipe_status)})
        rows = self.rows
        attempt_rows = self.attempt_rows
        app = ApplicationActivities(
            registry=StaticPlatformRegistry([adapter]),
            notifier=self._shared_notifier(),
            recipes=recipes,
            uow=lambda: InMemoryUnitOfWork(rows, attempt_rows=attempt_rows),
        )
        facts = StaticFactSource(_sample_facts())
        guide = self._shared_guide()
        # 별도 StubLLM 을 쓴다 — 하나를 공유하면 ResumeContentSchema/GuidePatchSchema 호출이
        # 같은 payload 큐를 순서대로 소비해서 스키마가 안 맞는 값을 뽑아갈 수 있다.
        resume = ResumeActivities(
            SimpleResumeGenerator(
                StubLLM(payloads=list(_RESUME_PAYLOADS)), idgen, facts, _sample_profile(), guide
            ),
            SimpleResumeReviewer(facts),
            StubPdfRenderer(store),
        )
        browser = BrowserActivities(ReplayExecutor(clock, fail_selectors=self.fail_selectors))
        guide_activities = GuideActivities(StubLLM(payloads=list(self.guide_patch_payloads)), guide)
        return [*app.all(), *resume.all(), *browser.all(), *guide_activities.all()]

    def container(self, *, settings: Settings | None = None) -> Container:
        """FastAPI 테스트용 `Container`. `activities()` 가 쓰는 것과 같은 `rows`/notifier 를

        공유한다 — 그래야 워크플로우가 workers 쪽에서 persist 한 상태를 API 의 GET 이 그대로
        읽고, 웹훅이 소비하는 nonce 도 activity 가 발급한 것과 일치한다.
        """
        idgen = UuidIdGen()
        clock = SystemClock()
        store = InMemoryBlobStore()
        llm = StubLLM(payloads=list(_RESUME_PAYLOADS))
        facts = StaticFactSource(_sample_facts())
        profile = _sample_profile()
        guide = self._shared_guide()
        rows = self.rows
        attempt_rows = self.attempt_rows
        resolved_settings = settings or Settings(
            notifier="console", storage="memory", llm_provider="stub"
        )
        return Container(
            settings=resolved_settings,
            clock=clock,
            idgen=idgen,
            store=store,
            llm=llm,
            notifier=self._shared_notifier(telegram=resolved_settings.notifier == "telegram"),
            uow=lambda: InMemoryUnitOfWork(rows, attempt_rows=attempt_rows),
            registry=StaticPlatformRegistry([FixturePlatformAdapter(eligible=self.eligible)]),
            recipes=InMemoryRecipeSource({"fixture": sample_recipe(status=self.recipe_status)}),
            executor=ReplayExecutor(clock, fail_selectors=self.fail_selectors),
            generator=SimpleResumeGenerator(llm, idgen, facts, profile, guide),
            reviewer=SimpleResumeReviewer(facts),
            pdf=StubPdfRenderer(store),
            job_sources=[FixtureJobSource()],
            matching_config=StaticMatchingConfigSource(),
            facts=facts,
            profile=profile,
            guide=guide,
        )

    def states(self, application_id: str) -> list[str]:
        return [str(r.state) for r in self.rows.get(application_id, [])]

    def row(self, application_id: str, state: str) -> PersistState | None:
        """멱등 upsert 때문에 같은 상태는 1행으로 합쳐진다. 그 행의 최신 값을 본다."""
        for r in self.rows.get(application_id, []):
            if str(r.state) == state:
                return r
        return None

    def attempts(self, application_id: str) -> list[ApplicationAttempt]:
        return list(self.attempt_rows.get(application_id, []))

    def attempt(self, application_id: str, attempt_no: int) -> ApplicationAttempt | None:
        """멱등 upsert 때문에 같은 시도 번호는 1행으로 합쳐진다. 그 행의 최신 값을 본다."""
        for a in self.attempt_rows.get(application_id, []):
            if a.attempt == attempt_no:
                return a
        return None
