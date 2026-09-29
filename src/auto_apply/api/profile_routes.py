"""인적사항 · 경험 · 답변KB REST (§A7). 규칙은 ProfileService 에 있고 여기는 HTTP 모양만 맞춘다."""

from fastapi import APIRouter, Response, status

from auto_apply.api.deps import ContainerDep
from auto_apply.api.schemas import AnswerBody, ExperienceBody, ProfileBody
from auto_apply.contracts.experience import Experience
from auto_apply.contracts.knowledge import Answer
from auto_apply.contracts.profile import Profile
from auto_apply.services.profile import DEFAULT_USER_ID

router = APIRouter(prefix="/api", tags=["profile"])
_USER = DEFAULT_USER_ID


@router.get("/profile")
async def get_profile(c: ContainerDep) -> Profile:
    return await c.profiles.get_profile(_USER)


@router.put("/profile")
async def put_profile(body: ProfileBody, c: ContainerDep) -> Profile:
    return await c.profiles.save_profile(body.to_profile(_USER))


@router.get("/experiences")
async def list_experiences(c: ContainerDep) -> list[Experience]:
    return await c.profiles.list_experiences(_USER)


@router.post("/experiences", status_code=status.HTTP_201_CREATED)
async def create_experience(body: ExperienceBody, c: ContainerDep) -> Experience:
    return await c.profiles.create_experience(_USER, body.model_dump())


@router.get("/experiences/{experience_id}")
async def get_experience(experience_id: str, c: ContainerDep) -> Experience:
    return await c.profiles.get_experience(_USER, experience_id)


@router.put("/experiences/{experience_id}")
async def put_experience(experience_id: str, body: ExperienceBody, c: ContainerDep) -> Experience:
    return await c.profiles.update_experience(_USER, experience_id, body.model_dump())


@router.delete("/experiences/{experience_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_experience(experience_id: str, c: ContainerDep) -> Response:
    await c.profiles.delete_experience(_USER, experience_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/answers")
async def list_answers(c: ContainerDep) -> list[Answer]:
    return await c.profiles.list_answers(_USER)


@router.post("/answers", status_code=status.HTTP_201_CREATED)
async def create_answer(body: AnswerBody, c: ContainerDep) -> Answer:
    return await c.profiles.create_answer(
        _USER, body.question, body.answer, body.source_application_id
    )


@router.get("/answers/{answer_id}")
async def get_answer(answer_id: str, c: ContainerDep) -> Answer:
    return await c.profiles.get_answer(_USER, answer_id)


@router.put("/answers/{answer_id}")
async def put_answer(answer_id: str, body: AnswerBody, c: ContainerDep) -> Answer:
    return await c.profiles.update_answer(
        _USER, answer_id, body.question, body.answer, body.source_application_id
    )


@router.delete("/answers/{answer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_answer(answer_id: str, c: ContainerDep) -> Response:
    await c.profiles.delete_answer(_USER, answer_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
