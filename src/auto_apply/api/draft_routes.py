"""온보딩 추출 초안 REST (§A7). 규칙은 ProfileDraftService 에 있고 여기는 HTTP 모양만 맞춘다.

흐름: 이력서 파일을 `POST /api/profile/drafts/upload`(multipart `file`, 문서로 저장하지 않음) 또는
저장된 문서로 `POST /api/profile/drafts {document_id}` → 목록 `GET` 으로 이어서 검토 →
고치면 `PUT` → `POST .../confirm` 에 고른 항목만 보내 본 프로필에 병합(초안은 지워진다).
"""

from fastapi import APIRouter, Request, Response, status
from pydantic import Field

from auto_apply.ai.profile_extraction import ProfileExtraction
from auto_apply.api.deps import ContainerDep
from auto_apply.api.multipart import read_single_file
from auto_apply.contracts._base import Frozen
from auto_apply.services.profile import DEFAULT_USER_ID
from auto_apply.services.profile_draft_types import (
    DraftConfirmation,
    DraftSelection,
    DraftSummary,
    ProfileDraft,
)

router = APIRouter(prefix="/api/profile/drafts", tags=["onboarding"])
_USER = DEFAULT_USER_ID
# v2 yaml 한 파일의 상한 — 실제 v2 facts.yaml 은 수십 KB 다.
_MAX_YAML_CHARS = 1_000_000


class ExtractBody(Frozen):
    document_id: str  # 업로드한 PDF·DOCX 이력서


class V2ImportBody(Frozen):
    profile_yaml: str | None = Field(default=None, max_length=_MAX_YAML_CHARS)
    facts_yaml: str | None = Field(default=None, max_length=_MAX_YAML_CHARS)


@router.get("")
async def list_drafts(c: ContainerDep) -> list[DraftSummary]:
    return await c.drafts.list_drafts(_USER)


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def extract_uploaded_file(request: Request, c: ContainerDep) -> ProfileDraft:
    """multipart/form-data 의 `file` 하나(pdf·docx). 주민번호 꼴은 LLM 전에 가리고 개수만 남긴다."""
    upload = await read_single_file(request, field="file", max_bytes=c.uploads.max_document_bytes)
    return await c.drafts.extract_from_file(_USER, upload.filename, upload.data)


@router.post("", status_code=status.HTTP_201_CREATED)
async def extract_draft(body: ExtractBody, c: ContainerDep) -> ProfileDraft:
    return await c.drafts.extract_from_document(_USER, body.document_id)


@router.post("/v2-import", status_code=status.HTTP_201_CREATED)
async def import_v2(body: V2ImportBody, c: ContainerDep) -> ProfileDraft:
    return await c.drafts.import_v2(_USER, body.profile_yaml, body.facts_yaml)


@router.get("/{draft_id}")
async def get_draft(draft_id: str, c: ContainerDep) -> ProfileDraft:
    return await c.drafts.get_draft(_USER, draft_id)


@router.put("/{draft_id}")
async def put_draft(draft_id: str, body: ProfileExtraction, c: ContainerDep) -> ProfileDraft:
    return await c.drafts.update_draft(_USER, draft_id, body)


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(draft_id: str, c: ContainerDep) -> Response:
    await c.drafts.delete_draft(_USER, draft_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{draft_id}/confirm")
async def confirm_draft(draft_id: str, body: DraftSelection, c: ContainerDep) -> DraftConfirmation:
    return await c.drafts.confirm(_USER, draft_id, body)
