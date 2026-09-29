"""SQLite 프로필·지식베이스 저장소 (§A7, ports/profile_store.py). 세션은 SqliteUnitOfWork 가 준다.

sqlite.py 와 같은 이유로 ORM 엔티티를 쓰지 않고 테이블(Core)로만 읽고 쓴다 — Core 쓰기 뒤에
identity map 의 낡은 객체를 읽지 않게.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply.adapters.repository.models import AnswerRow, DocumentRow, ExperienceRow, ProfileRow
from auto_apply.contracts._base import ensure_identifier_free
from auto_apply.contracts.experience import Experience
from auto_apply.contracts.knowledge import Answer, DocumentMeta
from auto_apply.contracts.profile import Profile
from auto_apply.domain.enums import DocumentKind
from auto_apply.domain.errors import AnswerKeyConflict


def _to_db(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(tzinfo=None)


def _from_db(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC)


class SqliteProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: str) -> Profile | None:
        payload = await self._session.scalar(
            select(ProfileRow.payload).where(ProfileRow.user_id == user_id)
        )
        return None if payload is None else Profile.model_validate(payload)

    async def save(self, profile: Profile) -> None:
        ensure_identifier_free(profile)
        payload = profile.model_dump(mode="json")
        await self._session.execute(
            sqlite_insert(ProfileRow)
            .values(user_id=profile.user_id, payload=payload)
            .on_conflict_do_update(index_elements=[ProfileRow.user_id], set_={"payload": payload})
        )


class SqliteExperienceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, experience_id: str) -> Experience | None:
        payload = await self._session.scalar(
            select(ExperienceRow.payload).where(ExperienceRow.id == experience_id)
        )
        return None if payload is None else Experience.model_validate(payload)

    async def list_for_user(self, user_id: str) -> list[Experience]:
        payloads = await self._session.scalars(
            select(ExperienceRow.payload)
            .where(ExperienceRow.user_id == user_id)
            .order_by(ExperienceRow.position, ExperienceRow.id)
        )
        return [Experience.model_validate(p) for p in payloads]

    async def save(self, experience: Experience) -> None:
        ensure_identifier_free(experience)
        payload = experience.model_dump(mode="json")
        updated = await self._session.execute(
            update(ExperienceRow)
            .where(ExperienceRow.id == experience.id)
            .values(user_id=experience.user_id, payload=payload)
        )
        if updated.rowcount:  # type: ignore[attr-defined]
            return
        last = await self._session.scalar(
            select(func.max(ExperienceRow.position)).where(
                ExperienceRow.user_id == experience.user_id
            )
        )
        await self._session.execute(
            insert(ExperienceRow).values(
                id=experience.id,
                user_id=experience.user_id,
                position=(last or 0) + 1,
                payload=payload,
            )
        )

    async def delete(self, experience_id: str) -> bool:
        res = await self._session.execute(
            delete(ExperienceRow).where(ExperienceRow.id == experience_id)
        )
        return bool(res.rowcount)  # type: ignore[attr-defined]


_ANSWERS = AnswerRow.__table__
_DOCUMENTS = DocumentRow.__table__


def _answer(row: Row[Any]) -> Answer:
    return Answer(
        id=row.id,
        user_id=row.user_id,
        question_key=row.question_key,
        answer=row.answer,
        source_application_id=row.source_application_id,
        updated_at=_from_db(row.updated_at),
    )


class SqliteAnswerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, answer_id: str) -> Answer | None:
        res = await self._session.execute(select(_ANSWERS).where(AnswerRow.id == answer_id))
        row = res.first()
        return None if row is None else _answer(row)

    async def find(self, user_id: str, question_key: str) -> Answer | None:
        res = await self._session.execute(
            select(_ANSWERS).where(
                AnswerRow.user_id == user_id, AnswerRow.question_key == question_key
            )
        )
        row = res.first()
        return None if row is None else _answer(row)

    async def list_for_user(self, user_id: str) -> list[Answer]:
        res = await self._session.execute(
            select(_ANSWERS).where(AnswerRow.user_id == user_id).order_by(AnswerRow.question_key)
        )
        return [_answer(r) for r in res]

    async def save(self, answer: Answer) -> None:
        ensure_identifier_free(answer)
        # 제약 위반(IntegrityError)으로 알아내면 세션 트랜잭션이 깨진다 — 먼저 조회로 거른다.
        holder = await self._session.scalar(
            select(AnswerRow.id).where(
                AnswerRow.user_id == answer.user_id,
                AnswerRow.question_key == answer.question_key,
            )
        )
        if holder is not None and holder != answer.id:
            raise AnswerKeyConflict(f"question_key 가 이미 다른 답변({holder})에 쓰였다")
        values = {
            "user_id": answer.user_id,
            "question_key": answer.question_key,
            "answer": answer.answer,
            "source_application_id": answer.source_application_id,
            "updated_at": _to_db(answer.updated_at),
        }
        await self._session.execute(
            sqlite_insert(AnswerRow)
            .values(id=answer.id, **values)
            .on_conflict_do_update(index_elements=[AnswerRow.id], set_=values)
        )

    async def delete(self, answer_id: str) -> bool:
        res = await self._session.execute(delete(AnswerRow).where(AnswerRow.id == answer_id))
        return bool(res.rowcount)  # type: ignore[attr-defined]


def _document(row: Row[Any]) -> DocumentMeta:
    return DocumentMeta(
        id=row.id,
        user_id=row.user_id,
        kind=DocumentKind(row.kind),
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        blob_key=row.blob_key,
        created_at=_from_db(row.created_at),
        fact_ids=list(row.fact_ids),
    )


class SqliteDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, document_id: str) -> DocumentMeta | None:
        res = await self._session.execute(select(_DOCUMENTS).where(DocumentRow.id == document_id))
        row = res.first()
        return None if row is None else _document(row)

    async def list_for_user(self, user_id: str) -> list[DocumentMeta]:
        res = await self._session.execute(
            select(_DOCUMENTS)
            .where(DocumentRow.user_id == user_id)
            .order_by(DocumentRow.created_at, DocumentRow.id)
        )
        return [_document(r) for r in res]

    async def save(self, document: DocumentMeta) -> None:
        ensure_identifier_free(document)
        values = {
            "user_id": document.user_id,
            "kind": str(document.kind),
            "filename": document.filename,
            "content_type": document.content_type,
            "size_bytes": document.size_bytes,
            "blob_key": document.blob_key,
            "created_at": _to_db(document.created_at),
            "fact_ids": list(document.fact_ids),
        }
        await self._session.execute(
            sqlite_insert(DocumentRow)
            .values(id=document.id, **values)
            .on_conflict_do_update(index_elements=[DocumentRow.id], set_=values)
        )

    async def delete(self, document_id: str) -> bool:
        res = await self._session.execute(delete(DocumentRow).where(DocumentRow.id == document_id))
        return bool(res.rowcount)  # type: ignore[attr-defined]
