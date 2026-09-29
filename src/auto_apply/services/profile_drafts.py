"""온보딩 추출 — 이력서 파일·v2 yaml → 프로필 **초안** → 사용자 확정 병합 (§A7).

추출 자체(텍스트·주민번호 꼴 가림·LLM)는 services/resume_extraction.py.

초안은 사용자가 확정하기 전까지 본 프로필과 떨어져 있다(저장: services/draft_store.py).
LLM 출력은 `ProfileExtraction` 스키마를 통과한 것만 초안이 된다(절대 규칙 4). 확정은 고른 항목만
한 트랜잭션으로 병합하고, 경험은 `build_experience` 로 id 를 새로 발급받는다.
"""

from collections.abc import Callable
from typing import Any

import structlog

from auto_apply.ai.profile_extraction import ProfileExtraction
from auto_apply.contracts.experience import Experience
from auto_apply.contracts.profile import Profile
from auto_apply.domain.errors import InvalidInput, NotFound
from auto_apply.domain.unique_identifiers import reject_unique_identifiers
from auto_apply.domain.uploads import validate_upload
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.llm import LLMClient
from auto_apply.ports.repository import UnitOfWork
from auto_apply.ports.storage import BlobStore
from auto_apply.ports.text_extract import DocumentTextExtractor
from auto_apply.services.draft_merge import experience_data, merge_profile
from auto_apply.services.draft_store import DraftStore
from auto_apply.services.profile import build_experience
from auto_apply.services.profile_draft_types import (
    DraftConfirmation,
    DraftSelection,
    DraftSource,
    DraftSummary,
    ProfileDraft,
)
from auto_apply.services.resume_extraction import extract_profile
from auto_apply.services.uploads import UploadService
from auto_apply.services.v2_import import import_v2_yaml

log = structlog.get_logger(__name__)


class ProfileDraftService:
    def __init__(
        self,
        uow: Callable[[], UnitOfWork],
        store: BlobStore,
        uploads: UploadService,
        extractor: DocumentTextExtractor,
        llm: LLMClient,
        clock: Clock,
        idgen: IdGen,
        *,
        max_text_chars: int = 50_000,
        max_reprompts: int = 2,
    ) -> None:
        self._uow = uow
        self._drafts = DraftStore(store)
        self._uploads = uploads
        self._extractor = extractor
        self._llm = llm
        self._clock = clock
        self._idgen = idgen
        self._max_text_chars = max_text_chars
        self._max_reprompts = max_reprompts

    # ── 초안 만들기 ─────────────────────────────────────────────────────────
    async def extract_from_file(self, user_id: str, filename: str, data: bytes) -> ProfileDraft:
        """온보딩: 이력서 파일을 문서로 저장하지 않고 바로 추출한다 (주민번호 꼴은 가린다)."""
        name, content_type = validate_upload(
            filename, data, max_bytes=self._uploads.max_document_bytes
        )
        return await self._extract(user_id, data, content_type, name, None)

    async def extract_from_document(self, user_id: str, document_id: str) -> ProfileDraft:
        meta, data = await self._uploads.read_document(user_id, document_id)
        return await self._extract(user_id, data, meta.content_type, meta.filename, document_id)

    async def import_v2(
        self, user_id: str, profile_yaml: str | None, facts_yaml: str | None
    ) -> ProfileDraft:
        content = import_v2_yaml(profile_yaml, facts_yaml)
        return await self._create(user_id, DraftSource.V2_YAML, content)

    async def _extract(
        self,
        user_id: str,
        data: bytes,
        content_type: str,
        filename: str,
        document_id: str | None,
    ) -> ProfileDraft:
        # 초안에 남는 값이라 LLM 을 부르기 전에 본다 — 호출 뒤 저장 단계에서 떨어지면 헛수고다.
        reject_unique_identifiers(filename, where="파일명")
        content, redacted = await extract_profile(
            self._extractor,
            self._llm,
            data,
            content_type,
            max_text_chars=self._max_text_chars,
            max_reprompts=self._max_reprompts,
        )
        return await self._create(
            user_id,
            DraftSource.RESUME,
            content,
            document_id=document_id,
            source_filename=filename,
            redacted_identifiers=redacted,
        )

    async def _create(
        self, user_id: str, source: DraftSource, content: ProfileExtraction, **extra: Any
    ) -> ProfileDraft:
        draft = ProfileDraft(
            id=self._idgen.new_id("draft"),
            user_id=user_id,
            source=source,
            created_at=self._clock.now(),
            content=content,
            **extra,
        )
        await self._drafts.save(draft)
        log.info(
            "profile draft created",
            application_id=None,
            run_id=None,
            draft_id=draft.id,
            source=source.value,
            experiences=len(content.experiences),
            redacted_identifiers=draft.redacted_identifiers,
        )
        return draft

    # ── 조회·편집·삭제 ──────────────────────────────────────────────────────
    async def list_drafts(self, user_id: str) -> list[DraftSummary]:
        return [DraftSummary.of(d) for d in await self._drafts.list(user_id)]

    async def get_draft(self, user_id: str, draft_id: str) -> ProfileDraft:
        return await self._drafts.get(user_id, draft_id)

    async def update_draft(
        self, user_id: str, draft_id: str, content: ProfileExtraction
    ) -> ProfileDraft:
        """검토 중 사용자가 고친 값으로 초안 내용을 통째로 바꾼다 — 같은 스키마로 다시 검증된다."""
        current = await self.get_draft(user_id, draft_id)
        draft = ProfileDraft.model_validate({**current.model_dump(), "content": content})
        await self._drafts.save(draft)
        return draft

    async def delete_draft(self, user_id: str, draft_id: str) -> None:
        if not await self._drafts.delete(user_id, draft_id):
            raise NotFound("초안을 찾을 수 없다")

    # ── 확정 ────────────────────────────────────────────────────────────────
    async def confirm(
        self, user_id: str, draft_id: str, selection: DraftSelection
    ) -> DraftConfirmation:
        """고른 항목만 한 트랜잭션으로 병합하고, 커밋된 뒤 초안을 지운다."""
        draft = await self.get_draft(user_id, draft_id)
        items = draft.content.experiences
        indexes = list(dict.fromkeys(selection.experience_indexes))
        if any(not 0 <= i < len(items) for i in indexes):
            raise InvalidInput("experience_indexes 에 초안에 없는 위치가 있다")
        fields = set(selection.profile_fields)
        profile: Profile | None = None
        created: list[Experience] = []
        async with self._uow() as uow:
            if fields:
                current = await uow.profiles.get(user_id)
                profile = merge_profile(current, draft.content.profile, fields, user_id)
                await uow.profiles.save(profile)
            for i in indexes:
                exp = await build_experience(
                    uow, self._idgen, user_id, self._idgen.new_id("exp"), experience_data(items[i])
                )
                await uow.experiences.save(exp)
                created.append(exp)
            await uow.commit()
        await self._drafts.delete(user_id, draft_id)
        return DraftConfirmation(profile=profile, experiences=created)
