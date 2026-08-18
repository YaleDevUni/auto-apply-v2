"""Composition root — 구현체 선택이 존재하는 유일한 파일 (ARCHITECTURE.md §11.4).

여기 말고 어디서도 어댑터를 생성하지 않는다. import-linter 가 이를 강제한다.
"""

from dataclasses import dataclass

from auto_apply.adapters.clock.system import SystemClock, UuidIdGen
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.notifier.console import ConsoleNotifier
from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.config import Settings
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.notifier import Notifier
from auto_apply.ports.storage import BlobStore


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    clock: Clock
    idgen: IdGen
    store: BlobStore
    llm: LLMClient
    notifier: Notifier


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
            return StubLLM()
        case "anthropic":
            raise NotImplementedError("AnthropicLLM 은 M3 에서 추가한다")


def _build_notifier(cfg: Settings, idgen: IdGen) -> Notifier:
    match cfg.notifier:
        case "console":
            return ConsoleNotifier(idgen)
        case "telegram":
            raise NotImplementedError("TelegramNotifier 는 M1 에서 추가한다")


def build_container(cfg: Settings) -> Container:
    idgen = UuidIdGen()
    return Container(
        settings=cfg,
        clock=SystemClock(),
        idgen=idgen,
        store=_build_store(cfg),
        llm=_build_llm(cfg),
        notifier=_build_notifier(cfg, idgen),
    )
