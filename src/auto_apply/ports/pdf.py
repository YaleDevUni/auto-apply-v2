from typing import Protocol

from auto_apply.contracts.dto import RenderedPdf, ResumeDraft


class PdfRenderer(Protocol):
    async def render(self, draft: ResumeDraft) -> RenderedPdf: ...
