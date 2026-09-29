"""Composition root — 구현체 선택이 존재하는 유일한 파일 (§A2).

여기 말고 어디서도 어댑터를 생성하지 않는다. import-linter 가 이를 강제한다.
데이터 디렉터리 준비(마이그레이션 포함)도 어댑터를 아는 이 파일이 맡는다 — api 는 부르기만 한다.
"""

from collections.abc import Callable
from dataclasses import dataclass

from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.facts.repository import RepositoryFactSource
from auto_apply.adapters.guide.file import FileGuideSource
from auto_apply.adapters.guide.static import StaticGuideSource
from auto_apply.adapters.llm.anthropic import AnthropicLLM
from auto_apply.adapters.llm.claude_code_cli import ClaudeCodeCliLLM
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.pdf.stub import StubPdfRenderer
from auto_apply.adapters.profile.repository import RepositoryProfileSource
from auto_apply.adapters.repository.memory import InMemoryDatabase, InMemoryUnitOfWork
from auto_apply.adapters.repository.migrate import MigrationError, upgrade_to_head
from auto_apply.adapters.repository.sqlite import sqlite_uow_factory
from auto_apply.adapters.resume.simple import SimpleResumeGenerator, SimpleResumeReviewer
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.config import Settings
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.facts import FactSource
from auto_apply.ports.guide import GuideSource
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.pdf import PdfRenderer
from auto_apply.ports.profile import ProfileSource
from auto_apply.ports.repository import UnitOfWork
from auto_apply.ports.resume import ResumeGenerator, ResumeReviewer
from auto_apply.ports.storage import BlobStore
from auto_apply.runner.job_runner import JobRunner
from auto_apply.services.document import DocumentService


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
    documents: DocumentService
    runner: JobRunner


class StartupError(RuntimeError):
    """기동 준비 실패. 메시지는 사람이 읽을 한 줄(무엇이·어디서·왜) — 진입점이 그대로 보여준다."""


def prepare_data_dir(cfg: Settings) -> None:
    """첫 기동에도 그대로 뜨게: 데이터 디렉터리를 만들고 스키마를 head 로 올린다 (§A1).

    이미 head 면 Alembic 이 아무것도 하지 않으니 매 기동 호출해도 된다. 업그레이드는 원자적이라
    실패해도 스키마는 실패 전 그대로다(migrations/env.py).
    """
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise StartupError(f"데이터 디렉터리를 만들 수 없다 — {cfg.data_dir}: {e}") from e
    if cfg.repository == "sqlite":
        try:
            upgrade_to_head(cfg.database_url)
        except MigrationError as e:
            raise StartupError(str(e)) from e


def _build_store(cfg: Settings) -> BlobStore:
    match cfg.storage:
        case "memory":
            return InMemoryBlobStore()
        case "local":
            return LocalBlobStore(cfg.files_dir)


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
            db = InMemoryDatabase()
            return lambda: InMemoryUnitOfWork(db)
        case "sqlite":
            return sqlite_uow_factory(cfg.database_url)


def _build_guide(cfg: Settings) -> GuideSource:
    match cfg.guide_source:
        case "static":
            return StaticGuideSource()
        case "file":
            return FileGuideSource(cfg.guide_dir)


def build_container(cfg: Settings) -> Container:
    idgen = UuidIdGen()
    clock = SystemClock()
    store = _build_store(cfg)
    llm = _build_llm(cfg)
    uow = _build_uow(cfg)
    # 프로필·경험은 repository 에 있다 (§A7) — 이력서 파이프라인은 같은 UoW 로 읽는다.
    facts: FactSource = RepositoryFactSource(uow)
    profile: ProfileSource = RepositoryProfileSource(uow)
    guide = _build_guide(cfg)
    generator = SimpleResumeGenerator(
        llm,
        idgen,
        facts,
        profile,
        guide,
        max_project_blocks=cfg.resume_max_project_blocks,
        max_career_blocks_per_entity=cfg.resume_max_career_blocks_per_entity,
    )
    reviewer = SimpleResumeReviewer(facts)
    # §A7: WeasyPrint 는 제거했고 Chrome `page.pdf()` 구현은 M5 에서 같은 port 로 붙인다.
    pdf = StubPdfRenderer(store)
    return Container(
        settings=cfg,
        clock=clock,
        idgen=idgen,
        store=store,
        llm=llm,
        uow=uow,
        generator=generator,
        reviewer=reviewer,
        pdf=pdf,
        facts=facts,
        profile=profile,
        guide=guide,
        documents=DocumentService(generator, reviewer, pdf),
        runner=JobRunner(),
    )
