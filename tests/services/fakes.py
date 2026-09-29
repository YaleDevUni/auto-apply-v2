"""서비스 테스트용 결정론적 Clock·IdGen, 초안 서비스 조립."""

from datetime import UTC, datetime

from auto_apply.adapters.extract.pdf_docx import PdfDocxTextExtractor
from auto_apply.adapters.llm.stub import StubLLM
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.services.profile_drafts import ProfileDraftService
from auto_apply.services.uploads import UploadService


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 30, tzinfo=UTC)


class SeqIds:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self, prefix: str = "") -> str:
        self.n += 1
        return f"{prefix}_{self.n}"


class RecordingLLM(StubLLM):
    """StubLLM 과 같되 받은 프롬프트(cache_prefix + prompt)를 남긴다."""

    def __init__(self, payloads: list[dict[str, object]] | None = None) -> None:
        super().__init__(payloads=payloads)
        self.prompts: list[str] = []

    async def structured(self, prompt, schema, *, max_tokens=2048, cache_prefix=""):
        self.prompts.append(cache_prefix + prompt)
        return await super().structured(prompt, schema, max_tokens=max_tokens)


def make_draft_service(uow, *, llm=None, extractor=None, store=None) -> ProfileDraftService:
    store = store or InMemoryBlobStore()
    clock, ids = FixedClock(), SeqIds()
    extractor = extractor or PdfDocxTextExtractor()
    uploads = UploadService(uow, store, extractor, clock, ids)
    return ProfileDraftService(
        uow,
        store,
        uploads,
        extractor,
        llm or RecordingLLM(),
        clock,
        ids,
    )
