"""BrowserToolbox 가 서비스에서 받는 통로 (§A5). 구현은 UploadService·ProfileService 가 만족한다."""

from typing import Protocol

from auto_apply.contracts.knowledge import Answer, DocumentMeta


class DocumentReader(Protocol):
    """앱이 관리하는 문서만 읽는 통로 (UploadService 가 만족한다). 없거나 남의 것이면 NotFound."""

    async def read_document(self, user_id: str, document_id: str) -> tuple[DocumentMeta, bytes]: ...


class AnswerSink(Protocol):
    """ask_user 의 답을 답변 KB 에 남기는 통로 (ProfileService 가 만족한다, D10).

    같은 질문 키가 있으면 갱신한다.
    """

    async def remember_answer(
        self, user_id: str, question: str, answer: str, *, application_id: str | None
    ) -> Answer: ...
