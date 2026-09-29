"""UploadService — 사용자가 올린 고정 파일(이력서·포트폴리오 원본, D11) (§A7).

바이트는 BlobStore(`files/`), 메타는 repository. 형식·크기·파일명 규칙은 domain/uploads.py.
"""

from collections.abc import Callable
from pathlib import PurePosixPath

from auto_apply.contracts.knowledge import DocumentMeta
from auto_apply.domain.enums import DocumentKind
from auto_apply.domain.errors import NotFound
from auto_apply.domain.uploads import validate_upload
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.repository import UnitOfWork
from auto_apply.ports.storage import BlobStore

DEFAULT_MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


class UploadService:
    def __init__(
        self,
        uow: Callable[[], UnitOfWork],
        store: BlobStore,
        clock: Clock,
        idgen: IdGen,
        *,
        max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
    ) -> None:
        self._uow = uow
        self._store = store
        self._clock = clock
        self._idgen = idgen
        self.max_document_bytes = max_document_bytes

    async def list_documents(self, user_id: str) -> list[DocumentMeta]:
        async with self._uow() as uow:
            return await uow.documents.list_for_user(user_id)

    async def get_document(self, user_id: str, document_id: str) -> DocumentMeta:
        async with self._uow() as uow:
            return await _owned_document(uow, user_id, document_id)

    async def read_document(self, user_id: str, document_id: str) -> tuple[DocumentMeta, bytes]:
        meta = await self.get_document(user_id, document_id)
        return meta, await self._store.get(meta.blob_key)

    async def upload_document(self, user_id: str, filename: str, data: bytes) -> DocumentMeta:
        name, content_type = validate_upload(filename, data, max_bytes=self.max_document_bytes)
        doc_id = self._idgen.new_id("doc")
        # 메타를 먼저 만든다 — 파일명에 고유식별정보가 있으면 바이트를 쓰기 전에 거부된다.
        meta = DocumentMeta(
            id=doc_id,
            user_id=user_id,
            kind=DocumentKind.UPLOADED,
            filename=name,
            content_type=content_type,
            size_bytes=len(data),
            # 사용자 파일명은 경로에 쓰지 않는다 — 표시는 메타의 filename 으로.
            blob_key=f"documents/{user_id}/{doc_id}{PurePosixPath(name).suffix.lower()}",
            created_at=self._clock.now(),
        )
        await self._store.put(meta.blob_key, data, content_type=content_type)
        try:
            async with self._uow() as uow:
                await uow.documents.save(meta)
                await uow.commit()
        except BaseException:
            # 메타 없는 바이트(고아 파일)를 남기지 않는다.
            await self._store.delete(meta.blob_key)
            raise
        return meta

    async def delete_document(self, user_id: str, document_id: str) -> None:
        """메타·경험의 첨부 참조를 한 트랜잭션으로 지우고, 커밋된 뒤에 바이트를 지운다.

        순서가 거꾸로면 커밋 실패 시 메타는 남고 바이트만 사라진 문서가 생긴다.
        """
        async with self._uow() as uow:
            meta = await _owned_document(uow, user_id, document_id)
            for exp in await uow.experiences.list_for_user(user_id):
                if document_id in exp.document_ids:
                    kept = [d for d in exp.document_ids if d != document_id]
                    await uow.experiences.save(exp.model_copy(update={"document_ids": kept}))
            await uow.documents.delete(document_id)
            await uow.commit()
        await self._store.delete(meta.blob_key)


async def _owned_document(uow: UnitOfWork, user_id: str, document_id: str) -> DocumentMeta:
    meta = await uow.documents.get(document_id)
    if meta is None or meta.user_id != user_id:
        raise NotFound("문서를 찾을 수 없다")
    return meta
