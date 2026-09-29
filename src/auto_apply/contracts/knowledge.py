"""답변KB · 문서 메타 (§A7).

Answer: 지원서 문항에 사용자가 한 번 답한 값. 에이전트가 먼저 조회하고 없으면 `ask_user`(D10).
질문 키 정규화는 ProfileService(T1.2) 몫이다 — 저장소는 받은 키를 그대로 유일 키로 쓴다.

DocumentMeta: 파일 바이트는 BlobStore(`files/`)에, 여기는 메타만.
생성 문서는 근거 fact_ids 를 남긴다.
"""

from pydantic import AwareDatetime, Field

from auto_apply.contracts._base import IdentifierFree
from auto_apply.domain.enums import DocumentKind


class Answer(IdentifierFree):
    id: str
    user_id: str
    question_key: str  # (user_id, question_key) 유일
    answer: str
    source_application_id: str | None = None  # 이 답을 처음 받은 지원 건
    updated_at: AwareDatetime


class DocumentMeta(IdentifierFree):
    id: str
    user_id: str
    kind: DocumentKind = DocumentKind.UPLOADED
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)
    blob_key: str
    created_at: AwareDatetime
    fact_ids: list[str] = Field(default_factory=list)  # 생성 문서의 근거 (GENERATED 일 때)
