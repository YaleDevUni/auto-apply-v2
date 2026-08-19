"""테스트 공용 빌더.

워크플로우 테스트는 bootstrap 을 쓰지 않고 필요한 어댑터만 직접 조립한다.
그래야 "이 테스트가 무엇을 대역으로 쓰는지" 가 테스트 안에서 드러난다.
"""

from dataclasses import dataclass, field

from auto_apply.activities.application import ApplicationActivities
from auto_apply.activities.browser import BrowserActivities
from auto_apply.activities.resume import ResumeActivities
from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.executor.replay import ReplayExecutor
from auto_apply.adapters.job_source.fixture import FixtureJobSource
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.matching_config.static import StaticMatchingConfigSource
from auto_apply.adapters.notifier.console import ConsoleNotifier
from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.platform.fixture import FixturePlatformAdapter
from auto_apply.adapters.platform.registry import StaticPlatformRegistry
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.bootstrap import Container
from auto_apply.config import Settings
from auto_apply.contracts.dto import PersistState
from auto_apply.contracts.recipe import Action, ActionType, AutomationRecipe
from auto_apply.ports.notifier import Notifier

JOB_URL = "https://fixture.local/jobs/1"


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
    eligible: bool = True
    reject_reason: str = ""
    verified: bool = True
    fail_selectors: frozenset[str] = frozenset()
    recipe_status: str = "active"
    # activities() 와 container() 가 같은 인스턴스를 써야 nonce 흐름을 끝까지 검증할 수 있다
    # (웹훅이 소비하는 nonce 는 워커 쪽 activity 가 발급한 것과 같은 객체 상태여야 한다).
    notifier: Notifier | None = None

    def _shared_notifier(self) -> Notifier:
        if self.notifier is None:
            self.notifier = ConsoleNotifier(UuidIdGen())
        return self.notifier

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
        app = ApplicationActivities(
            registry=StaticPlatformRegistry([adapter]),
            notifier=self._shared_notifier(),
            recipes=recipes,
            uow=lambda: InMemoryUnitOfWork(rows),
        )
        resume = ResumeActivities(
            SimpleResumeGenerator(StubLLM(responses=["요건에 맞춘 경력 요약"] * 20), idgen),
            SimpleResumeReviewer(),
            StubPdfRenderer(store),
        )
        browser = BrowserActivities(ReplayExecutor(clock, fail_selectors=self.fail_selectors))
        return [*app.all(), *resume.all(), *browser.all()]

    def container(self, *, settings: Settings | None = None) -> Container:
        """FastAPI 테스트용 `Container`. `activities()` 가 쓰는 것과 같은 `rows`/notifier 를

        공유한다 — 그래야 워크플로우가 workers 쪽에서 persist 한 상태를 API 의 GET 이 그대로
        읽고, 웹훅이 소비하는 nonce 도 activity 가 발급한 것과 일치한다.
        """
        idgen = UuidIdGen()
        clock = SystemClock()
        store = InMemoryBlobStore()
        llm = StubLLM(responses=["요건에 맞춘 경력 요약"] * 20)
        rows = self.rows
        return Container(
            settings=settings
            or Settings(notifier="console", storage="memory", llm_provider="stub"),
            clock=clock,
            idgen=idgen,
            store=store,
            llm=llm,
            notifier=self._shared_notifier(),
            uow=lambda: InMemoryUnitOfWork(rows),
            registry=StaticPlatformRegistry([FixturePlatformAdapter(eligible=self.eligible)]),
            recipes=InMemoryRecipeSource({"fixture": sample_recipe(status=self.recipe_status)}),
            executor=ReplayExecutor(clock, fail_selectors=self.fail_selectors),
            generator=SimpleResumeGenerator(llm, idgen),
            reviewer=SimpleResumeReviewer(),
            pdf=StubPdfRenderer(store),
            job_sources=[FixtureJobSource()],
            matching_config=StaticMatchingConfigSource(),
        )

    def states(self, application_id: str) -> list[str]:
        return [str(r.state) for r in self.rows.get(application_id, [])]

    def row(self, application_id: str, state: str) -> PersistState | None:
        """멱등 upsert 때문에 같은 상태는 1행으로 합쳐진다. 그 행의 최신 값을 본다."""
        for r in self.rows.get(application_id, []):
            if str(r.state) == state:
                return r
        return None
