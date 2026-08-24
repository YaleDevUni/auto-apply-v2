"""테스트 공용 빌더.

워크플로우 테스트는 bootstrap 을 쓰지 않고 필요한 어댑터만 직접 조립한다.
그래야 "이 테스트가 무엇을 대역으로 쓰는지" 가 테스트 안에서 드러난다.
"""

from dataclasses import dataclass, field

from auto_apply.activities.application import ApplicationActivities
from auto_apply.activities.browser import BrowserActivities
from auto_apply.activities.guide import GuideActivities
from auto_apply.activities.repair import RepairActivities
from auto_apply.activities.resume import ResumeActivities
from auto_apply.adapters.attachments.fixture import FixtureAttachmentManager
from auto_apply.adapters.attachments.registry import StaticAttachmentRegistry
from auto_apply.adapters.checkpoint.memory import InMemoryCheckpointStore
from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.credentials.static import StaticCredentialSource
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
from auto_apply.adapters.portfolio.static import StaticPortfolioSource
from auto_apply.adapters.profile.static import StaticProfileSource
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork, ResumeRows
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.adapters.web_agent.replay import ReplayWebAgentExecutor
from auto_apply.bootstrap import Container
from auto_apply.config import Settings
from auto_apply.contracts.dto import (
    ApplicationAttempt,
    CachedResume,
    DecisionRequest,
    DecisionTicket,
    NotifyEvent,
    PersistState,
    ScheduleConfig,
)
from auto_apply.contracts.fact import Fact
from auto_apply.contracts.job import JobRecord
from auto_apply.contracts.profile import Profile
from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.domain.enums import RevisionScope
from auto_apply.ports.notifier import Notifier
from auto_apply.ports.platform import PlatformRegistry

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


def _sample_portfolio() -> StaticPortfolioSource:
    return StaticPortfolioSource()  # 빈 매핑 — 카테고리 판정은 이 테스트 스위트의 관심사가 아니다


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
    # dry-run-indicator-backlog: 승인 요청에 실린 mode 배지 값을 워크플로우 테스트에서
    # 검증하기 위한 로그.
    requests: list[DecisionRequest] = field(default_factory=list)
    # (요청, nonce) 짝. `last_ticket` 은 application_id 하나만 키로 쓰는데, 한 워크플로우가
    # 같은 id 로 승인을 두 번 요청하면(§2.4a: recipe 파손 확정 → 승격 승인) 뒤엣것이
    # 앞엣것을 덮어써서 "어느 요청의 nonce 인가"를 구분할 수 없다 — 그래서 순서대로 다 남긴다.
    issued: list[tuple[DecisionRequest, str]] = field(default_factory=list)

    async def request_decision(self, req: DecisionRequest) -> DecisionTicket:
        self.requests.append(req)
        ticket = await self.inner.request_decision(req)
        self.last_ticket[req.application_id] = ticket.nonce
        self.issued.append((req, ticket.nonce))
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

    async def resend_decision(self, application_id: str, nonce: str) -> None:
        """텔레그램 채팅 에이전트의 resend_pending_decision 도구가 쓴다 (telegram/agent.py)."""
        assert isinstance(self.inner, TelegramNotifier)
        await self.inner.resend_decision(application_id, nonce)


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


class _BrokenResumeCacheRepository:
    """get_cached_resume activity 자체가 죽는 상황(§2.3, 2026-08-24 GC메디아이 사고)을
    흉내낸다 — 배포 직후 worker 가 재시작 전이라 activity 가 미등록인 경우가 실측 사례다.
    조회만 깨졌을 뿐이라 save 는 정상 동작해야 한다(폴백 생성 결과를 다음 재지원용으로
    캐시에 남기는 정상 흐름).
    """

    def __init__(self, rows: ResumeRows) -> None:
        self._rows = rows

    async def get(self, application_id: str) -> CachedResume | None:
        raise RuntimeError("resume cache lookup broken (simulated)")

    async def save(self, resume: CachedResume) -> None:
        self._rows[resume.application_id] = resume


@dataclass
class Harness:
    """워크플로우 테스트용 조립체. rows 로 DB projection 을 검사한다."""

    rows: dict[str, list[PersistState]] = field(default_factory=dict)
    attempt_rows: dict[str, list[ApplicationAttempt]] = field(default_factory=dict)
    job_rows: dict[tuple[str, str], JobRecord] = field(default_factory=dict)
    schedule_config_rows: dict[str, ScheduleConfig] = field(default_factory=dict)
    resume_rows: dict[str, CachedResume] = field(default_factory=dict)
    eligible: bool = True
    reject_reason: str = ""
    verified: bool = True
    fail_selectors: frozenset[str] = frozenset()
    recipe_status: str = "active"
    # wanted-application-caution-indicators-backlog: caution_documents 배지가 job.description
    # 을 그대로 스캔하므로, 그 감지를 워크플로우 테스트에서 검증하려면 채워야 한다.
    job_description: str = ""
    # activities() 와 container() 가 같은 인스턴스를 써야 GET 이 workers 쪽 persist 결과를
    # 그대로 읽는다(rows 공유) — nonce 검증 자체는 더 이상 여기 있지 않다(워크플로우가 한다).
    notifier: _NonceSpy | None = None
    # 마찬가지로 activities() 와 container() 가 공유해야 한다 — REVISE(general) 테스트가
    # apply_guide_patch(worker 쪽)로 바뀐 내용을 이 인스턴스로 확인한다.
    guide: StaticGuideSource | None = None
    # StubLLM 이 GuidePatchSchema 요청에도 순서대로 payload 를 내주므로, guide patch 를 쓰는
    # 테스트는 여기 채워서 다음 propose_guide_patch 호출이 이 값을 쓰게 한다.
    guide_patch_payloads: list[dict[str, object]] = field(default_factory=list)
    # get_cached_resume 활동 자체가 죽는 경우(예: 배포 직후 worker 미재시작으로 activity
    # 미등록)를 흉내낸다 — 2026-08-24 GC메디아이 사고 회귀 테스트용
    # (test_cache_lookup_failure_falls_back_to_normal_generation_instead_of_crashing).
    cache_lookup_broken: bool = False
    # RecipeDiffSchema 용 — repair(§2.4)를 쓰는 테스트가 채운다. 비어 있으면(기본값)
    # propose_recipe_diff 가 빈 payload({}) 를 받아 필수 필드 누락으로 LLMSchemaViolation을
    # 내고, repair 는 그대로 실패해서(포기) 기존 "M4 까지는 사람에게 넘긴다" 테스트가
    # 그대로 성립한다.
    repair_diff_payloads: list[dict[str, object]] = field(default_factory=list)
    # activities() 의 executor 는 ReplayExecutor 라 이 store 를 안 쓴다 — telegram/bridge.py
    # 의 ca/cr(§ supervised-checkpoint-design) 콜백을 container() 로 테스트할 때만 관찰용으로
    # 공유한다.
    checkpoint_store: InMemoryCheckpointStore = field(default_factory=InMemoryCheckpointStore)
    # container() 의 기본 registry(FixturePlatformAdapter 하나)를 덮어쓸 때만 채운다 — apply_by_url
    # 처럼 platform 이 "wanted" 인 어댑터가 필요한 테스트용(apply_intake.py 의 wanted 한정 체크).
    registry: PlatformRegistry | None = None
    # activities() 와 container() 가 같은 인스턴스를 봐야 하는 것들. recipe 는 §2.4a 격리
    # (quarantine) 이후 상태를 테스트가 직접 확인해야 해서, blob store 는 테스트가 DOM
    # 스냅샷을 미리 심어 판정(diagnose_recipe_failure)을 태우려면 필요해서 공유한다.
    _recipes: InMemoryRecipeSource | None = None
    _store: InMemoryBlobStore | None = None

    @property
    def recipes(self) -> InMemoryRecipeSource:
        if self._recipes is None:
            self._recipes = InMemoryRecipeSource(
                {"fixture": sample_recipe(status=self.recipe_status)}
            )
        return self._recipes

    @property
    def store(self) -> InMemoryBlobStore:
        if self._store is None:
            self._store = InMemoryBlobStore()
        return self._store

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
        store = self.store
        adapter = FixturePlatformAdapter(
            eligible=self.eligible,
            reject_reason=self.reject_reason,
            verified=self.verified,
            description=self.job_description,
        )
        recipes = self.recipes
        rows = self.rows
        attempt_rows = self.attempt_rows
        schedule_config_rows = self.schedule_config_rows
        resume_rows = self.resume_rows
        cache_lookup_broken = self.cache_lookup_broken

        def _make_uow() -> InMemoryUnitOfWork:
            uow = InMemoryUnitOfWork(
                rows,
                attempt_rows=attempt_rows,
                schedule_config_rows=schedule_config_rows,
                resume_rows=resume_rows,
            )
            if cache_lookup_broken:
                uow.resumes = _BrokenResumeCacheRepository(resume_rows)  # type: ignore[assignment]
            return uow

        app = ApplicationActivities(
            registry=StaticPlatformRegistry([adapter]),
            notifier=self._shared_notifier(),
            recipes=recipes,
            uow=_make_uow,
        )
        facts = StaticFactSource(_sample_facts())
        guide = self._shared_guide()
        # 별도 StubLLM 을 쓴다 — 하나를 공유하면 ResumeContentSchema/GuidePatchSchema 호출이
        # 같은 payload 큐를 순서대로 소비해서 스키마가 안 맞는 값을 뽑아갈 수 있다.
        resume = ResumeActivities(
            SimpleResumeGenerator(
                StubLLM(payloads=list(_RESUME_PAYLOADS)),
                idgen,
                facts,
                _sample_profile(),
                _sample_portfolio(),
                guide,
            ),
            SimpleResumeReviewer(facts),
            StubPdfRenderer(store),
        )
        browser = BrowserActivities(ReplayExecutor(clock, fail_selectors=self.fail_selectors))
        guide_activities = GuideActivities(StubLLM(payloads=list(self.guide_patch_payloads)), guide)
        # 별도 StubLLM — resume/guide payload 큐와 섞이면 스키마가 안 맞는 값을 뽑아갈 수 있다
        # (위 guide_activities 와 같은 이유). recipes 는 ApplicationActivities 와 같은 인스턴스를
        # 공유해야 save/promote 결과를 load_active_recipe 가 그대로 본다.
        repair = RepairActivities(StubLLM(payloads=list(self.repair_diff_payloads)), recipes, store)
        return [*app.all(), *resume.all(), *browser.all(), *guide_activities.all(), *repair.all()]

    def container(self, *, settings: Settings | None = None) -> Container:
        """FastAPI 테스트용 `Container`. `activities()` 가 쓰는 것과 같은 `rows`/notifier/

        recipes/store 를 공유한다 — 그래야 워크플로우가 workers 쪽에서 persist 한 상태를 API 의
        GET 이 그대로 읽고, 웹훅이 소비하는 nonce 도 activity 가 발급한 것과 일치한다.
        """
        idgen = UuidIdGen()
        clock = SystemClock()
        store = self.store
        llm = StubLLM(payloads=list(_RESUME_PAYLOADS))
        facts = StaticFactSource(_sample_facts())
        profile = _sample_profile()
        portfolio = _sample_portfolio()
        guide = self._shared_guide()
        rows = self.rows
        attempt_rows = self.attempt_rows
        job_rows = self.job_rows
        schedule_config_rows = self.schedule_config_rows
        resume_rows = self.resume_rows
        resolved_settings = settings or Settings(
            notifier="console", storage="memory", llm_provider="stub"
        )
        return Container(
            settings=resolved_settings,
            clock=clock,
            idgen=idgen,
            store=store,
            llm=llm,
            chat_llm=llm,
            notifier=self._shared_notifier(telegram=resolved_settings.notifier == "telegram"),
            uow=lambda: InMemoryUnitOfWork(
                rows,
                job_rows,
                attempt_rows=attempt_rows,
                schedule_config_rows=schedule_config_rows,
                resume_rows=resume_rows,
            ),
            registry=self.registry
            or StaticPlatformRegistry([FixturePlatformAdapter(eligible=self.eligible)]),
            recipes=self.recipes,
            executor=ReplayExecutor(clock, fail_selectors=self.fail_selectors),
            generator=SimpleResumeGenerator(llm, idgen, facts, profile, portfolio, guide),
            reviewer=SimpleResumeReviewer(facts),
            pdf=StubPdfRenderer(store),
            job_sources=[FixtureJobSource()],
            matching_config=StaticMatchingConfigSource(),
            facts=facts,
            profile=profile,
            portfolio=portfolio,
            guide=guide,
            credentials=StaticCredentialSource(),
            web_agent=ReplayWebAgentExecutor(clock),
            attachments=StaticAttachmentRegistry([FixtureAttachmentManager()]),
            checkpoint_store=self.checkpoint_store,
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

    def cached_resume(self, application_id: str) -> CachedResume | None:
        """§2.3 이력서 재사용 캐시 — 워크플로우가 실제로 캐시를 남겼는지 테스트가 확인한다."""
        return self.resume_rows.get(application_id)
