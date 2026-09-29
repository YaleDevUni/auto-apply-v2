"""업로드 문서 REST (§A7) — 이력서·포트폴리오 원본 같은 고정 파일.

형식·크기 규칙은 ProfileService 에 있다.
"""

from urllib.parse import quote

from fastapi import APIRouter, Request, Response, status

from auto_apply.api.deps import ContainerDep
from auto_apply.api.multipart import read_single_file
from auto_apply.contracts.knowledge import DocumentMeta
from auto_apply.services.profile import DEFAULT_USER_ID

router = APIRouter(prefix="/api", tags=["documents"])
_USER = DEFAULT_USER_ID


@router.get("/documents")
async def list_documents(c: ContainerDep) -> list[DocumentMeta]:
    return await c.uploads.list_documents(_USER)


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(request: Request, c: ContainerDep) -> DocumentMeta:
    """multipart/form-data 의 `file` 필드 하나 (pdf·docx·png·jpg)."""
    upload = await read_single_file(request, field="file", max_bytes=c.uploads.max_document_bytes)
    return await c.uploads.upload_document(_USER, upload.filename, upload.data)


@router.get("/documents/{document_id}")
async def get_document(document_id: str, c: ContainerDep) -> DocumentMeta:
    return await c.uploads.get_document(_USER, document_id)


@router.get("/documents/{document_id}/content")
async def get_document_content(document_id: str, c: ContainerDep) -> Response:
    meta, data = await c.uploads.read_document(_USER, document_id)
    return Response(
        content=data,
        media_type=meta.content_type,
        headers={
            # 저장한 content type 은 업로드 때 바이트 시그니처로 확인한 값이다. 브라우저가 다시
            # 추측(sniff)해 HTML 로 실행하지 않게 막는다.
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(meta.filename)}",
        },
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, c: ContainerDep) -> Response:
    await c.uploads.delete_document(_USER, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
