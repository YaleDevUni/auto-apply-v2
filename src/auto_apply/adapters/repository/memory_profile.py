"""메모리 프로필·지식베이스 저장소 — 테스트 대역 (§A2 구현 2개, ports/profile_store.py).

dict 삽입 순서가 곧 목록 순서다: 기존 키에 대입하면 자리가 그대로라
"수정해도 순서 유지" 계약과 같다.
"""

from auto_apply.contracts._base import ensure_identifier_free
from auto_apply.contracts.experience import Experience
from auto_apply.contracts.knowledge import Answer, DocumentMeta
from auto_apply.contracts.profile import Profile
from auto_apply.domain.errors import AnswerKeyConflict


class InMemoryProfileRepository:
    def __init__(self, rows: dict[str, Profile]) -> None:
        self._rows = rows

    async def get(self, user_id: str) -> Profile | None:
        return self._rows.get(user_id)

    async def save(self, profile: Profile) -> None:
        ensure_identifier_free(profile)
        self._rows[profile.user_id] = profile


class InMemoryExperienceRepository:
    def __init__(self, rows: dict[str, Experience]) -> None:
        self._rows = rows

    async def get(self, experience_id: str) -> Experience | None:
        return self._rows.get(experience_id)

    async def list_for_user(self, user_id: str) -> list[Experience]:
        return [e for e in self._rows.values() if e.user_id == user_id]

    async def save(self, experience: Experience) -> None:
        ensure_identifier_free(experience)
        self._rows[experience.id] = experience

    async def delete(self, experience_id: str) -> bool:
        return self._rows.pop(experience_id, None) is not None


class InMemoryAnswerRepository:
    def __init__(self, rows: dict[str, Answer]) -> None:
        self._rows = rows

    async def get(self, answer_id: str) -> Answer | None:
        return self._rows.get(answer_id)

    async def find(self, user_id: str, question_key: str) -> Answer | None:
        return next(
            (
                a
                for a in self._rows.values()
                if a.user_id == user_id and a.question_key == question_key
            ),
            None,
        )

    async def list_for_user(self, user_id: str) -> list[Answer]:
        return sorted(
            (a for a in self._rows.values() if a.user_id == user_id),
            key=lambda a: a.question_key,
        )

    async def save(self, answer: Answer) -> None:
        ensure_identifier_free(answer)
        holder = await self.find(answer.user_id, answer.question_key)
        if holder is not None and holder.id != answer.id:
            raise AnswerKeyConflict(f"question_key 가 이미 다른 답변({holder.id})에 쓰였다")
        self._rows[answer.id] = answer

    async def delete(self, answer_id: str) -> bool:
        return self._rows.pop(answer_id, None) is not None


class InMemoryDocumentRepository:
    def __init__(self, rows: dict[str, DocumentMeta]) -> None:
        self._rows = rows

    async def get(self, document_id: str) -> DocumentMeta | None:
        return self._rows.get(document_id)

    async def list_for_user(self, user_id: str) -> list[DocumentMeta]:
        return sorted(
            (d for d in self._rows.values() if d.user_id == user_id),
            key=lambda d: (d.created_at, d.id),
        )

    async def save(self, document: DocumentMeta) -> None:
        ensure_identifier_free(document)
        self._rows[document.id] = document

    async def delete(self, document_id: str) -> bool:
        return self._rows.pop(document_id, None) is not None
