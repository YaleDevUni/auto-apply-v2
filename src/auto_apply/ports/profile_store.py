"""프로필·지식베이스 저장소 (§A7). `UnitOfWork` 한 트랜잭션 안에서 쓴다 (ports/repository.py).

공통 계약:
- `save` 는 id 기준 upsert 다. 새 항목은 목록 끝에 붙고, 기존 항목을 고쳐도 목록 순서는 그대로다
  (경험 순서 = 이력서 등장 순서).
- `get` 은 없으면 None, `delete` 는 지웠으면 True.
- `save` 는 고유식별정보가 든 값을 `UniqueIdentifierRejected` 로 거부하고 아무것도 쓰지 않는다
  (절대 규칙 5). DTO 생성 시점 검사를 `model_copy(update=...)`·`model_construct` 로 건너뛴 값도
  여기서 막힌다 — 방어 심층.
"""

from typing import Protocol

from auto_apply.contracts.experience import Experience
from auto_apply.contracts.knowledge import Answer, DocumentMeta
from auto_apply.contracts.profile import Profile


class ProfileRepository(Protocol):
    async def get(self, user_id: str) -> Profile | None: ...

    async def save(self, profile: Profile) -> None: ...


class ExperienceRepository(Protocol):
    async def get(self, experience_id: str) -> Experience | None: ...

    async def list_for_user(self, user_id: str) -> list[Experience]: ...

    async def save(self, experience: Experience) -> None: ...

    async def delete(self, experience_id: str) -> bool: ...


class AnswerRepository(Protocol):
    async def get(self, answer_id: str) -> Answer | None: ...

    async def find(self, user_id: str, question_key: str) -> Answer | None: ...

    async def list_for_user(self, user_id: str) -> list[Answer]: ...

    async def save(self, answer: Answer) -> None:
        """같은 사용자의 **다른 id** 가 이미 이 question_key 를 쓰면 `AnswerKeyConflict`.

        조용히 덮어쓰지 않는다 — 어느 답이 살아남을지는 서비스가 정할 일이다.
        """
        ...

    async def delete(self, answer_id: str) -> bool: ...


class DocumentRepository(Protocol):
    """메타만. 바이트는 BlobStore 에 있고, 둘을 같이 지우는 것은 서비스 몫이다."""

    async def get(self, document_id: str) -> DocumentMeta | None: ...

    async def list_for_user(self, user_id: str) -> list[DocumentMeta]: ...

    async def save(self, document: DocumentMeta) -> None: ...

    async def delete(self, document_id: str) -> bool: ...
