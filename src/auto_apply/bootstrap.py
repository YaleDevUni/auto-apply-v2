"""Composition root — 구현체 선택이 존재하는 유일한 파일 (ARCHITECTURE.md §11.4).

여기 말고 어디서도 어댑터를 생성하지 않는다. import-linter 가 이를 강제한다.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.executor.playwright import PlaywrightExecutor
from auto_apply.adapters.executor.replay import ReplayExecutor
from auto_apply.adapters.facts.static import StaticFactSource
from auto_apply.adapters.facts.yaml_file import YamlFactSource
from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.adapters.job_source.fixture import FixtureJobSource
from auto_apply.adapters.job_source.jasoseol import JasoseolJobSource
from auto_apply.adapters.job_source.saramin import SaraminJobSource
from auto_apply.adapters.job_source.wanted import WantedJobSource
from auto_apply.adapters.llm.anthropic import AnthropicLLM
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.matching_config.static import StaticMatchingConfigSource
from auto_apply.adapters.matching_config.yaml_file import YamlMatchingConfigSource
from auto_apply.adapters.notifier.console import ConsoleNotifier
from auto_apply.adapters.notifier.telegram import TelegramNotifier
from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.platform.fixture import FixturePlatformAdapter
from auto_apply.adapters.platform.registry import StaticPlatformRegistry
from auto_apply.adapters.recipe.jsonfile import JsonFileRecipeSource
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.adapters.repository.file import FileUnitOfWork
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.adapters.repository.postgres import sqlalchemy_uow_factory
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.config import Settings
from auto_apply.contracts.dto import PersistState
from auto_apply.contracts.job import JobRecord
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.executor import RecipeExecutor
from auto_apply.ports.facts import FactSource
from auto_apply.ports.job_source import JobSource
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.matching_config import MatchingConfigSource
from auto_apply.ports.notifier import Notifier
from auto_apply.ports.pdf import PdfRenderer
from auto_apply.ports.platform import PlatformRegistry
from auto_apply.ports.recipe_source import RecipeSource
from auto_apply.ports.repository import UnitOfWork
from auto_apply.ports.resume import ResumeGenerator, ResumeReviewer
from auto_apply.ports.storage import BlobStore


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    clock: Clock
    idgen: IdGen
    store: BlobStore
    llm: LLMClient
    notifier: Notifier
    uow: Callable[[], UnitOfWork]
    registry: PlatformRegistry
    recipes: RecipeSource
    executor: RecipeExecutor
    generator: ResumeGenerator
    reviewer: ResumeReviewer
    pdf: PdfRenderer
    job_sources: Sequence[JobSource]
    matching_config: MatchingConfigSource
    facts: FactSource


def _build_store(cfg: Settings) -> BlobStore:
    match cfg.storage:
        case "memory":
            return InMemoryBlobStore()
        case "local":
            return LocalBlobStore(cfg.data_dir)
        case "s3":
            raise NotImplementedError("S3BlobStore 는 M2 에서 추가한다")


def _build_llm(cfg: Settings) -> LLMClient:
    match cfg.llm_provider:
        case "stub":
            return StubLLM(responses=["stub 요약: 공고 요건에 맞춘 경력 정리"] * 10)
        case "anthropic":
            if not cfg.anthropic_api_key:
                raise ValueError("LLM_PROVIDER=anthropic 이면 ANTHROPIC_API_KEY 가 필요하다")
            return AnthropicLLM(cfg.anthropic_api_key, model=cfg.anthropic_model)


def _build_notifier(cfg: Settings, idgen: IdGen) -> Notifier:
    match cfg.notifier:
        case "console":
            return ConsoleNotifier(idgen)
        case "telegram":
            if not cfg.telegram_bot_token:
                raise ValueError("NOTIFIER=telegram 이면 TELEGRAM_BOT_TOKEN 이 필요하다")
            if not cfg.allowed_chat_ids:
                raise ValueError("NOTIFIER=telegram 이면 TELEGRAM_ALLOWED_CHAT_IDS 가 필요하다")
            return TelegramNotifier(cfg.telegram_bot_token, cfg.allowed_chat_ids, idgen)


def _build_uow(cfg: Settings) -> Callable[[], UnitOfWork]:
    match cfg.repository:
        case "memory":
            rows: dict[str, list[PersistState]] = {}
            job_rows: dict[tuple[str, str], JobRecord] = {}
            return lambda: InMemoryUnitOfWork(rows, job_rows)
        case "file":
            root = cfg.data_dir
            return lambda: FileUnitOfWork(root)
        case "postgres":
            return sqlalchemy_uow_factory(cfg.database_url)


def _build_executor(cfg: Settings, clock: Clock, store: BlobStore) -> RecipeExecutor:
    match cfg.executor:
        case "replay":
            return ReplayExecutor(clock)
        case "playwright":
            return PlaywrightExecutor(
                clock,
                store,
                auth_dir=cfg.data_dir / "auth",
                headless=cfg.playwright_headless,
            )


def _build_recipes(cfg: Settings) -> RecipeSource:
    path = cfg.data_dir / "recipes"
    if path.is_dir():
        return JsonFileRecipeSource(path)
    return InMemoryRecipeSource()


def _build_job_sources(cfg: Settings, store: BlobStore) -> Sequence[JobSource]:
    match cfg.job_source:
        case "fixture":
            return [FixtureJobSource()]
        case "live":
            # 플랫폼마다 커넥션 풀을 분리한다 — 한 플랫폼이 느려져도 나머지 수집에
            # 영향을 주지 않는다. 요청 정책(delay/retries)은 세 플랫폼이 함께
            # 검증된 값을 기본으로 쓴다 (adapters/job_source/_http.py).
            return [
                WantedJobSource(ThrottledClient()),
                SaraminJobSource(ThrottledClient()),
                JasoseolJobSource(ThrottledClient(), store),
            ]


def _build_matching_config(cfg: Settings) -> MatchingConfigSource:
    match cfg.matching_config:
        case "static":
            return StaticMatchingConfigSource()
        case "yaml":
            return YamlMatchingConfigSource(cfg.matching_config_path)


def _build_facts(cfg: Settings) -> FactSource:
    match cfg.facts_source:
        case "static":
            return StaticFactSource()
        case "yaml":
            return YamlFactSource(cfg.facts_path)


def build_container(cfg: Settings) -> Container:
    idgen = UuidIdGen()
    clock = SystemClock()
    store = _build_store(cfg)
    llm = _build_llm(cfg)
    facts = _build_facts(cfg)
    return Container(
        settings=cfg,
        clock=clock,
        idgen=idgen,
        store=store,
        llm=llm,
        notifier=_build_notifier(cfg, idgen),
        uow=_build_uow(cfg),
        registry=StaticPlatformRegistry([FixturePlatformAdapter()]),
        recipes=_build_recipes(cfg),
        executor=_build_executor(cfg, clock, store),
        generator=SimpleResumeGenerator(llm, idgen, facts),
        reviewer=SimpleResumeReviewer(facts),
        pdf=StubPdfRenderer(store),
        job_sources=_build_job_sources(cfg, store),
        matching_config=_build_matching_config(cfg),
        facts=facts,
    )
