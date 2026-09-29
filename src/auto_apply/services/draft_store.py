"""초안 저장 — BlobStore JSON `drafts/{user_id}/{draft_id}.json` (§A7).

검토가 끝나면 지우는 일회성 작업물이라 테이블을 두지 않는다. 목록은 `BlobStore.list_keys` 로 본다.
"""

import re

import structlog

from auto_apply.contracts._base import ensure_identifier_free
from auto_apply.domain.errors import BlobNotFound, NotFound
from auto_apply.ports.storage import BlobStore
from auto_apply.services.profile_draft_types import ProfileDraft

log = structlog.get_logger(__name__)

# 경로에 들어가는 값이라 모양을 못박는다 — `..` 같은 값으로 다른 blob 을 읽거나 지우지 못하게.
_DRAFT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class DraftStore:
    def __init__(self, store: BlobStore) -> None:
        self._store = store

    async def save(self, draft: ProfileDraft) -> None:
        # 검증을 건너뛴 값(`model_copy(update=)`)도 여기서 막는다 (절대 규칙 5).
        ensure_identifier_free(draft)
        await self._store.put(
            _key(draft.user_id, draft.id),
            draft.model_dump_json().encode(),
            content_type="application/json",
        )

    async def get(self, user_id: str, draft_id: str) -> ProfileDraft:
        try:
            raw = await self._store.get(_key(user_id, draft_id))
        except BlobNotFound:
            raise NotFound("초안을 찾을 수 없다") from None
        return ProfileDraft.model_validate_json(raw)

    async def delete(self, user_id: str, draft_id: str) -> bool:
        return await self._store.delete(_key(user_id, draft_id))

    async def list(self, user_id: str) -> list[ProfileDraft]:
        """최근 것부터. 읽을 수 없는 초안 파일은 건너뛴다(내용은 로그에 남기지 않는다)."""
        drafts: list[ProfileDraft] = []
        for key in await self._store.list_keys(f"drafts/{user_id}/"):
            try:
                drafts.append(ProfileDraft.model_validate_json(await self._store.get(key)))
            except (BlobNotFound, ValueError):
                log.warning("unreadable profile draft skipped", application_id=None, run_id=None)
        drafts.sort(key=lambda d: d.created_at, reverse=True)
        return [d for d in drafts if d.user_id == user_id]


def _key(user_id: str, draft_id: str) -> str:
    if not _DRAFT_ID.fullmatch(draft_id):
        raise NotFound("초안을 찾을 수 없다")
    return f"drafts/{user_id}/{draft_id}.json"
