"""설정값 → 어댑터 하나씩 (§A2). 조립(서로 잇기)은 container.py 가 한다."""

from collections.abc import Callable

from auto_apply.adapters.guide.file import FileGuideSource
from auto_apply.adapters.guide.static import StaticGuideSource
from auto_apply.adapters.llm.anthropic import AnthropicLLM
from auto_apply.adapters.llm.claude_code_cli import ClaudeCodeCliLLM
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.repository.memory import InMemoryDatabase, InMemoryUnitOfWork
from auto_apply.adapters.repository.sqlite import sqlite_uow_factory
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.config import Settings
from auto_apply.ports.guide import GuideSource
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.repository import UnitOfWork
from auto_apply.ports.storage import BlobStore


def build_store(cfg: Settings) -> BlobStore:
    match cfg.storage:
        case "memory":
            return InMemoryBlobStore()
        case "local":
            return LocalBlobStore(cfg.files_dir)


def build_llm(cfg: Settings) -> LLMClient:
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


def build_uow(cfg: Settings) -> Callable[[], UnitOfWork]:
    match cfg.repository:
        case "memory":
            # 클로저 밖에서 한 번만 만들어 공유해야 서로 다른 `c.uow()` 호출 사이에 쓴 값이 남는다.
            db = InMemoryDatabase()
            return lambda: InMemoryUnitOfWork(db)
        case "sqlite":
            return sqlite_uow_factory(cfg.database_url)


def build_guide(cfg: Settings) -> GuideSource:
    match cfg.guide_source:
        case "static":
            return StaticGuideSource()
        case "file":
            return FileGuideSource(cfg.guide_dir)
