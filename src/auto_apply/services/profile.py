"""ProfileService — 인적사항·경험·답변KB 유스케이스 (§A7). 업로드 문서는 services/uploads.py.

저장소가 강제하지 않는 규칙을 여기서 지킨다: id 발급(경험·fact·답변), fact id 의 사용자 단위
유일성, 경험의 첨부 문서 참조 검사, 답변KB 질문 키 정규화(domain/question_key.py).
고유식별정보 거부(절대 규칙 5)는 DTO·저장소가 막고, 이 서비스가 만든 값도 같은 DTO 검증을
거쳐서만 저장소에 닿는다.
"""

from collections.abc import Callable, Mapping
from typing import Any

from auto_apply.contracts.experience import Experience
from auto_apply.contracts.knowledge import Answer
from auto_apply.contracts.profile import Profile
from auto_apply.domain.errors import InvalidInput, NotFound
from auto_apply.domain.question_key import normalize_question_key
from auto_apply.ports.clock import Clock, IdGen
from auto_apply.ports.repository import UnitOfWork

# 로컬 1인 설치(D1)라 사용자는 하나다. 다중 사용자를 열 때 이 상수 하나만 요청 컨텍스트로 바꾼다.
DEFAULT_USER_ID = "local"


class ProfileService:
    def __init__(
        self,
        uow: Callable[[], UnitOfWork],
        clock: Clock,
        idgen: IdGen,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._idgen = idgen

    # ── 인적사항 ────────────────────────────────────────────────────────────
    async def get_profile(self, user_id: str) -> Profile:
        async with self._uow() as uow:
            profile = await uow.profiles.get(user_id)
        if profile is None:
            raise NotFound("인적사항이 아직 없다")
        return profile

    async def save_profile(self, profile: Profile) -> Profile:
        if not profile.name.strip():
            raise InvalidInput("이름은 비울 수 없다")
        async with self._uow() as uow:
            await uow.profiles.save(profile)
            await uow.commit()
        return profile

    # ── 경험 ────────────────────────────────────────────────────────────────
    async def list_experiences(self, user_id: str) -> list[Experience]:
        async with self._uow() as uow:
            return await uow.experiences.list_for_user(user_id)

    async def get_experience(self, user_id: str, experience_id: str) -> Experience:
        async with self._uow() as uow:
            return await _owned_experience(uow, user_id, experience_id)

    async def create_experience(self, user_id: str, data: Mapping[str, Any]) -> Experience:
        """`data` 는 id·user_id 없는 경험 초안. fact 의 `id` 가 비어 있으면 여기서 발급한다."""
        async with self._uow() as uow:
            exp = await self._build_experience(uow, user_id, self._idgen.new_id("exp"), data)
            await uow.experiences.save(exp)
            await uow.commit()
        return exp

    async def update_experience(
        self, user_id: str, experience_id: str, data: Mapping[str, Any]
    ) -> Experience:
        async with self._uow() as uow:
            await _owned_experience(uow, user_id, experience_id)
            exp = await self._build_experience(uow, user_id, experience_id, data)
            await uow.experiences.save(exp)
            await uow.commit()
        return exp

    async def delete_experience(self, user_id: str, experience_id: str) -> None:
        async with self._uow() as uow:
            await _owned_experience(uow, user_id, experience_id)
            await uow.experiences.delete(experience_id)
            await uow.commit()

    async def _build_experience(
        self, uow: UnitOfWork, user_id: str, experience_id: str, data: Mapping[str, Any]
    ) -> Experience:
        payload = {**data, "id": experience_id, "user_id": user_id}
        payload["facts"] = [self._with_fact_id(f) for f in data.get("facts", [])]
        payload["sections"] = [
            {**s, "facts": [self._with_fact_id(f) for f in s.get("facts", [])]}
            for s in data.get("sections", [])
        ]
        exp = Experience.model_validate(payload)
        # fact id 는 이력서 서술이 근거로 인용하는 키라 사용자 전체에서 유일해야 한다
        # (ground_check).
        others = {
            f.id
            for e in await uow.experiences.list_for_user(user_id)
            if e.id != experience_id
            for f in e.all_facts()
        }
        if any(f.id in others for f in exp.all_facts()):
            raise InvalidInput("fact id 가 다른 경험의 fact 와 겹친다")
        docs = {d.id for d in await uow.documents.list_for_user(user_id)}
        if any(doc_id not in docs for doc_id in exp.document_ids):
            raise InvalidInput("document_ids 에 없는 문서가 있다")
        return exp

    def _with_fact_id(self, fact: Mapping[str, Any]) -> dict[str, Any]:
        return {**fact, "id": fact.get("id") or self._idgen.new_id("fact")}

    # ── 답변KB ──────────────────────────────────────────────────────────────
    async def list_answers(self, user_id: str) -> list[Answer]:
        async with self._uow() as uow:
            return await uow.answers.list_for_user(user_id)

    async def get_answer(self, user_id: str, answer_id: str) -> Answer:
        async with self._uow() as uow:
            return await _owned_answer(uow, user_id, answer_id)

    async def find_answer(self, user_id: str, question: str) -> Answer | None:
        """에이전트가 문항을 만나면 먼저 부른다 (§A7). 없으면 None → `ask_user`."""
        async with self._uow() as uow:
            return await uow.answers.find(user_id, normalize_question_key(question))

    async def create_answer(
        self,
        user_id: str,
        question: str,
        answer: str,
        source_application_id: str | None = None,
    ) -> Answer:
        """같은 질문 키가 이미 있으면 `AnswerKeyConflict` — 덮어쓰기는 update 로 명시한다."""
        row = Answer(
            id=self._idgen.new_id("ans"),
            user_id=user_id,
            question_key=normalize_question_key(question),
            answer=answer,
            source_application_id=source_application_id,
            updated_at=self._clock.now(),
        )
        async with self._uow() as uow:
            await uow.answers.save(row)
            await uow.commit()
        return row

    async def update_answer(
        self,
        user_id: str,
        answer_id: str,
        question: str,
        answer: str,
        source_application_id: str | None = None,
    ) -> Answer:
        async with self._uow() as uow:
            current = await _owned_answer(uow, user_id, answer_id)
            row = Answer(
                id=answer_id,
                user_id=user_id,
                question_key=normalize_question_key(question),
                answer=answer,
                # 출처는 "처음 받은 지원 건"이라 사용자가 고쳐도 비워 보내면 유지한다.
                source_application_id=source_application_id or current.source_application_id,
                updated_at=self._clock.now(),
            )
            await uow.answers.save(row)
            await uow.commit()
        return row

    async def delete_answer(self, user_id: str, answer_id: str) -> None:
        async with self._uow() as uow:
            await _owned_answer(uow, user_id, answer_id)
            await uow.answers.delete(answer_id)
            await uow.commit()


async def _owned_experience(uow: UnitOfWork, user_id: str, experience_id: str) -> Experience:
    exp = await uow.experiences.get(experience_id)
    if exp is None or exp.user_id != user_id:
        raise NotFound("경험을 찾을 수 없다")
    return exp


async def _owned_answer(uow: UnitOfWork, user_id: str, answer_id: str) -> Answer:
    row = await uow.answers.get(answer_id)
    if row is None or row.user_id != user_id:
        raise NotFound("답변을 찾을 수 없다")
    return row
