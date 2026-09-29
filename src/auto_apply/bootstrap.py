"""Composition root — 구현체 선택이 존재하는 유일한 파일 (§A2).

여기 말고 어디서도 어댑터를 생성하지 않는다. import-linter 가 이를 강제한다.
"""

from collections.abc import Callable
from dataclasses import dataclass

from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.facts.static import StaticFactSource
from auto_apply.adapters.facts.yaml_file import YamlFactSource
from auto_apply.adapters.guide.file import FileGuideSource
from auto_apply.adapters.guide.static import StaticGuideSource
from auto_apply.adapters.llm.anthropic import AnthropicLLM
from auto_apply.adapters.llm.claude_code_cli import ClaudeCodeCliLLM
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.profile.static import StaticProfileSource
from auto_apply.adapters.profile.yaml_source import YamlProfileSource
from auto_apply.adapters.repository.file import FileUnitOfWork
from auto_apply.adapters.repository.memory import InMemoryUnitOfWork
from auto_apply.adapters.repository.postgres import sqlalchemy_uow_factory
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.adapters.storage.s3 import S3BlobStore
from auto_apply.config import Settings
from auto_apply.contracts.dto import PersistState
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.facts import FactSource
from auto_apply.ports.guide import GuideSource
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.pdf import PdfRenderer
from auto_apply.ports.profile import ProfileSource
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
    uow: Callable[[], UnitOfWork]
    generator: ResumeGenerator
    reviewer: ResumeReviewer
    pdf: PdfRenderer
    facts: FactSource
    profile: ProfileSource
    guide: GuideSource


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


def _build_uow(cfg: Settings) -> Callable[[], UnitOfWork]:
    match cfg.repository:
        case "memory":
            # 클로저 밖에서 한 번만 만들어 공유해야 서로 다른 `c.uow()` 호출 사이에 쓴 값이 남는다.
            rows: dict[str, list[PersistState]] = {}
            return lambda: InMemoryUnitOfWork(rows)
        case "file":
            root = cfg.data_dir
            return lambda: FileUnitOfWork(root)
        case "postgres":
            return sqlalchemy_uow_factory(cfg.database_url)


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


def _build_guide(cfg: Settings) -> GuideSource:
    match cfg.guide_source:
        case "static":
            return StaticGuideSource()
        case "file":
            return FileGuideSource(cfg.resume_guide_dir)


def build_container(cfg: Settings) -> Container:
    idgen = UuidIdGen()
    clock = SystemClock()
    store = _build_store(cfg)
    llm = _build_llm(cfg)
    facts = _build_facts(cfg)
    profile = _build_profile(cfg)
    guide = _build_guide(cfg)
    return Container(
        settings=cfg,
        clock=clock,
        idgen=idgen,
        store=store,
        llm=llm,
        uow=_build_uow(cfg),
        generator=SimpleResumeGenerator(
            llm,
            idgen,
            facts,
            profile,
            guide,
            max_project_blocks=cfg.resume_max_project_blocks,
            max_career_blocks_per_entity=cfg.resume_max_career_blocks_per_entity,
        ),
        reviewer=SimpleResumeReviewer(facts),
        # §A7: WeasyPrint 는 제거했고 Chrome `page.pdf()` 구현은 M5 에서 같은 port 로 붙인다.
        pdf=StubPdfRenderer(store),
        facts=facts,
        profile=profile,
        guide=guide,
    )
