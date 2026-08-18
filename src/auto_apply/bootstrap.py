"""Composition root — 구현체 선택이 존재하는 유일한 파일 (ARCHITECTURE.md §11.4).

여기 말고 어디서도 어댑터를 생성하지 않는다. import-linter 가 이를 강제한다.
"""

from collections.abc import Callable
from dataclasses import dataclass

from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.executor.replay import ReplayExecutor
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.notifier.console import ConsoleNotifier
from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.platform.fixture import FixturePlatformAdapter
from auto_apply.adapters.platform.registry import StaticPlatformRegistry
from auto_apply.adapters.recipe.jsonfile import JsonFileRecipeSource
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.adapters.repository.file import FileUnitOfWork
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.config import Settings
from auto_apply.contracts.dto import PersistState
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.executor import RecipeExecutor
from auto_apply.ports.llm import LLMClient
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
            raise NotImplementedError("AnthropicLLM 은 M3 에서 추가한다")


def _build_notifier(cfg: Settings, idgen: IdGen) -> Notifier:
    match cfg.notifier:
        case "console":
            return ConsoleNotifier(idgen)
        case "telegram":
            raise NotImplementedError("TelegramNotifier 는 M1 후반에 추가한다")


def _build_uow(cfg: Settings) -> Callable[[], UnitOfWork]:
    match cfg.repository:
        case "memory":
            rows: dict[str, list[PersistState]] = {}
            return lambda: InMemoryUnitOfWork(rows)
        case "file":
            root = cfg.data_dir
            return lambda: FileUnitOfWork(root)
        case "postgres":
            raise NotImplementedError("SqlAlchemyUnitOfWork 는 M2 에서 추가한다")


def _build_executor(cfg: Settings, clock: Clock) -> RecipeExecutor:
    match cfg.executor:
        case "replay":
            return ReplayExecutor(clock)
        case "playwright":
            raise NotImplementedError("PlaywrightExecutor 는 M2 에서 추가한다")


def _build_recipes(cfg: Settings) -> RecipeSource:
    path = cfg.data_dir / "recipes"
    if path.is_dir():
        return JsonFileRecipeSource(path)
    return InMemoryRecipeSource()


def build_container(cfg: Settings) -> Container:
    idgen = UuidIdGen()
    clock = SystemClock()
    store = _build_store(cfg)
    llm = _build_llm(cfg)
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
        executor=_build_executor(cfg, clock),
        generator=SimpleResumeGenerator(llm, idgen),
        reviewer=SimpleResumeReviewer(),
        pdf=StubPdfRenderer(store),
    )
