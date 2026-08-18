import json

from auto_apply.contracts.dto import RenderedPdf, ResumeDraft
from auto_apply.ports.storage import BlobStore


class StubPdfRenderer:
    """실제 PDF 렌더러(WeasyPrint 등)는 M2 에서 같은 port 로 교체한다."""

    def __init__(self, store: BlobStore) -> None:
        self._store = store

    async def render(self, draft: ResumeDraft) -> RenderedPdf:
        body = json.dumps(draft.content, ensure_ascii=False, indent=2).encode()
        key = f"resumes/{draft.resume_id}.json"
        await self._store.put(key, body, content_type="application/json")
        return RenderedPdf(blob_key=key, bytes_written=len(body))
