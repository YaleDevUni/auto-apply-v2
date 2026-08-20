"""Composition root — 구현체 선택이 존재하는 유일한 파일 (ARCHITECTURE.md §11.4).

여기 말고 어디서도 어댑터를 생성하지 않는다. import-linter 가 이를 강제한다.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from auto_apply.adapters.attachments.registry import StaticAttachmentRegistry
from auto_apply.adapters.attachments.wanted import WantedAttachmentManager
from auto_apply.adapters.checkpoint.file import FileCheckpointStore
from auto_apply.adapters.checkpoint.memory import InMemoryCheckpointStore
from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.credentials.json_queue import JsonQueueCredentialSource
from auto_apply.adapters.credentials.static import StaticCredentialSource
from auto_apply.adapters.executor._checkpoint import CheckpointWaiter
from auto_apply.adapters.executor.agent_browser import AgentBrowserExecutor
from auto_apply.adapters.executor.playwright import PlaywrightExecutor
from auto_apply.adapters.executor.replay import ReplayExecutor
from auto_apply.adapters.facts.static import StaticFactSource
from auto_apply.adapters.facts.yaml_file import YamlFactSource
from auto_apply.adapters.guide.file import FileGuideSource
from auto_apply.adapters.guide.static import StaticGuideSource
from auto_apply.adapters.job_source._http import ThrottledClient
from auto_apply.adapters.job_source.fixture import FixtureJobSource
from auto_apply.adapters.job_source.jasoseol import JasoseolJobSource
from auto_apply.adapters.job_source.saramin import SaraminJobSource
from auto_apply.adapters.job_source.wanted import WantedJobSource
from auto_apply.adapters.llm.anthropic import AnthropicLLM
from auto_apply.adapters.llm.claude_code_cli import ClaudeCodeCliLLM
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.matching_config.static import StaticMatchingConfigSource
from auto_apply.adapters.matching_config.yaml_file import YamlMatchingConfigSource
from auto_apply.adapters.notifier.console import ConsoleNotifier
from auto_apply.adapters.notifier.telegram import TelegramNotifier
from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.pdf.weasyprint import WeasyPrintPdfRenderer
from auto_apply.adapters.platform.fixture import FixturePlatformAdapter
from auto_apply.adapters.platform.registry import StaticPlatformRegistry
from auto_apply.adapters.platform.wanted import WantedPlatformAdapter
from auto_apply.adapters.portfolio.static import StaticPortfolioSource
from auto_apply.adapters.portfolio.yaml_source import YamlPortfolioSource
from auto_apply.adapters.profile.static import StaticProfileSource
from auto_apply.adapters.profile.yaml_source import YamlProfileSource
from auto_apply.adapters.recipe.jsonfile import JsonFileRecipeSource
from auto_apply.adapters.recipe.memory import InMemoryRecipeSource
from auto_apply.adapters.repository.file import FileUnitOfWork
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.adapters.repository.postgres import sqlalchemy_uow_factory
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.adapters.storage.s3 import S3BlobStore
from auto_apply.adapters.web_agent.aside_cli import AsideCliExecutor
from auto_apply.adapters.web_agent.replay import ReplayWebAgentExecutor
from auto_apply.config import Settings
from auto_apply.contracts.dto import PersistState
from auto_apply.contracts.job import JobRecord
from auto_apply.ports.attachments import AttachmentRegistry
from auto_apply.ports.checkpoint_store import CheckpointStore
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.credentials import CredentialSource
from auto_apply.ports.executor import RecipeExecutor
from auto_apply.ports.facts import FactSource
from auto_apply.ports.guide import GuideSource
from auto_apply.ports.job_source import JobSource
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.matching_config import MatchingConfigSource
from auto_apply.ports.notifier import Notifier
from auto_apply.ports.pdf import PdfRenderer
from auto_apply.ports.platform import PlatformRegistry
from auto_apply.ports.portfolio import PortfolioSource
from auto_apply.ports.profile import ProfileSource
from auto_apply.ports.recipe_source import RecipeSource
from auto_apply.ports.repository import UnitOfWork
from auto_apply.ports.resume import ResumeGenerator, ResumeReviewer
from auto_apply.ports.storage import BlobStore
from auto_apply.ports.web_agent import WebAgentExecutor


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
    profile: ProfileSource
    portfolio: PortfolioSource
    guide: GuideSource
    credentials: CredentialSource
    web_agent: WebAgentExecutor
    attachments: AttachmentRegistry
    checkpoint_store: CheckpointStore


def _build_store(cfg: Settings) -> BlobStore:
    match cfg.storage:
        case "memory":
            return InMemoryBlobStore()
        case "local":
            return LocalBlobStore(cfg.data_dir)
        case "s3":
            return S3BlobStore(
                endpoint_url=cfg.s3_endpoint_url,
                bucket=cfg.s3_bucket,
                access_key=cfg.s3_access_key,
                secret_key=cfg.s3_secret_key,
            )


def _build_llm(cfg: Settings) -> LLMClient:
    match cfg.llm_provider:
        case "stub":
            return StubLLM(responses=["stub 요약: 공고 요건에 맞춘 경력 정리"] * 10)
        case "anthropic":
            if not cfg.anthropic_api_key:
                raise ValueError("LLM_PROVIDER=anthropic 이면 ANTHROPIC_API_KEY 가 필요하다")
            return AnthropicLLM(cfg.anthropic_api_key, model=cfg.anthropic_model)
        case "claude_cli":
            return ClaudeCodeCliLLM(
                binary=cfg.claude_cli_binary,
                model=cfg.claude_cli_model,
                max_budget_usd=cfg.claude_cli_max_budget_usd,
            )


def _build_notifier(cfg: Settings, idgen: IdGen, store: BlobStore) -> Notifier:
    match cfg.notifier:
        case "console":
            return ConsoleNotifier(idgen)
        case "telegram":
            if not cfg.telegram_bot_token:
                raise ValueError("NOTIFIER=telegram 이면 TELEGRAM_BOT_TOKEN 이 필요하다")
            if not cfg.allowed_chat_ids:
                raise ValueError("NOTIFIER=telegram 이면 TELEGRAM_ALLOWED_CHAT_IDS 가 필요하다")
            return TelegramNotifier(
                cfg.telegram_bot_token, cfg.allowed_chat_ids, idgen, store=store
            )


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


def _build_checkpoint_store(cfg: Settings) -> CheckpointStore:
    match cfg.checkpoint_store:
        case "memory":
            return InMemoryCheckpointStore()
        case "file":
            return FileCheckpointStore(cfg.data_dir)


def _build_executor(
    cfg: Settings,
    clock: Clock,
    store: BlobStore,
    notifier: Notifier,
    checkpoint_store: CheckpointStore,
    idgen: IdGen,
) -> RecipeExecutor:
    match cfg.executor:
        case "replay":
            return ReplayExecutor(clock)
        case "playwright":
            checkpoint = CheckpointWaiter(
                notifier,
                checkpoint_store,
                store,
                idgen,
                timeout=timedelta(minutes=cfg.checkpoint_timeout_minutes),
                poll_interval=timedelta(seconds=cfg.checkpoint_poll_seconds),
            )
            return PlaywrightExecutor(
                clock,
                store,
                auth_dir=cfg.data_dir / "auth",
                headless=cfg.playwright_headless,
                checkpoint=checkpoint,
            )
        case "agent_browser":
            checkpoint = CheckpointWaiter(
                notifier,
                checkpoint_store,
                store,
                idgen,
                timeout=timedelta(minutes=cfg.checkpoint_timeout_minutes),
                poll_interval=timedelta(seconds=cfg.checkpoint_poll_seconds),
            )
            return AgentBrowserExecutor(
                clock,
                store,
                auth_dir=cfg.data_dir / "auth",
                binary=cfg.agent_browser_binary,
                headless=cfg.playwright_headless,
                checkpoint=checkpoint,
            )


def _build_recipes(cfg: Settings) -> RecipeSource:
    path = cfg.data_dir / "recipes"
    if path.is_dir():
        return JsonFileRecipeSource(path)
    return InMemoryRecipeSource()


def _build_registry(cfg: Settings) -> PlatformRegistry:
    # JOB_SOURCE 를 그대로 재사용한다 — "오프라인 픽스처 vs 실제 플랫폼" 이라는 같은 축이라
    # 별도 설정을 하나 더 두지 않았다. fixture.local 은 어차피 실 플랫폼 도메인과 안 겹친다.
    match cfg.job_source:
        case "fixture":
            return StaticPlatformRegistry([FixturePlatformAdapter()])
        case "live":
            # saramin/jasoseol 은 job_source(공고 수집) 는 있어도 PlatformAdapter(지원 실행)
            # 는 아직 없다 — 필요해지면 여기에 추가한다.
            return StaticPlatformRegistry(
                [WantedPlatformAdapter(ThrottledClient(), auth_dir=cfg.data_dir / "auth")]
            )


def _build_attachments(cfg: Settings) -> AttachmentRegistry:
    # JOB_SOURCE 와 달리 fixture 분기를 두지 않는다 — 첨부파일 정리는 항상 사람이 수동으로
    # 트리거하는 운영 스크립트라(resume_cleanup.py) 등록해 둬도 실제로 호출하기 전엔
    # storage_state 를 읽지 않는다(AuthRequired 는 list_attachments/delete_attachment 호출
    # 시점에만 난다).
    return StaticAttachmentRegistry([WantedAttachmentManager(auth_dir=cfg.data_dir / "auth")])


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


def _build_profile(cfg: Settings) -> ProfileSource:
    match cfg.profile_source:
        case "static":
            return StaticProfileSource()
        case "yaml":
            return YamlProfileSource(cfg.profile_path)


def _build_portfolio(cfg: Settings) -> PortfolioSource:
    match cfg.portfolio_source:
        case "static":
            return StaticPortfolioSource()
        case "yaml":
            return YamlPortfolioSource(cfg.portfolio_map_path)


def _build_guide(cfg: Settings) -> GuideSource:
    match cfg.guide_source:
        case "static":
            return StaticGuideSource()
        case "file":
            return FileGuideSource(cfg.resume_guide_dir)


def _build_credentials(cfg: Settings) -> CredentialSource:
    match cfg.credential_source:
        case "static":
            return StaticCredentialSource()
        case "json":
            return JsonQueueCredentialSource(cfg.credential_queue_path)


def _build_web_agent(
    cfg: Settings, clock: Clock, store: BlobStore, credentials: CredentialSource
) -> WebAgentExecutor:
    match cfg.web_agent:
        case "replay":
            return ReplayWebAgentExecutor(clock)
        case "aside_cli":
            return AsideCliExecutor(
                credentials,
                store,
                clock,
                binary=cfg.aside_cli_binary,
                account=cfg.aside_cli_account or None,
            )


def _build_pdf(cfg: Settings, store: BlobStore) -> PdfRenderer:
    match cfg.pdf_renderer:
        case "stub":
            return StubPdfRenderer(store)
        case "weasyprint":
            return WeasyPrintPdfRenderer(store)


def build_container(cfg: Settings) -> Container:
    idgen = UuidIdGen()
    clock = SystemClock()
    store = _build_store(cfg)
    llm = _build_llm(cfg)
    facts = _build_facts(cfg)
    profile = _build_profile(cfg)
    portfolio = _build_portfolio(cfg)
    guide = _build_guide(cfg)
    credentials = _build_credentials(cfg)
    notifier = _build_notifier(cfg, idgen, store)
    checkpoint_store = _build_checkpoint_store(cfg)
    return Container(
        settings=cfg,
        clock=clock,
        idgen=idgen,
        store=store,
        llm=llm,
        notifier=notifier,
        uow=_build_uow(cfg),
        registry=_build_registry(cfg),
        recipes=_build_recipes(cfg),
        executor=_build_executor(cfg, clock, store, notifier, checkpoint_store, idgen),
        generator=SimpleResumeGenerator(
            llm,
            idgen,
            facts,
            profile,
            portfolio,
            guide,
            max_project_blocks=cfg.resume_max_project_blocks,
            max_career_blocks_per_entity=cfg.resume_max_career_blocks_per_entity,
        ),
        reviewer=SimpleResumeReviewer(facts),
        pdf=_build_pdf(cfg, store),
        job_sources=_build_job_sources(cfg, store),
        matching_config=_build_matching_config(cfg),
        facts=facts,
        profile=profile,
        portfolio=portfolio,
        guide=guide,
        credentials=credentials,
        web_agent=_build_web_agent(cfg, clock, store, credentials),
        attachments=_build_attachments(cfg),
        checkpoint_store=checkpoint_store,
    )
