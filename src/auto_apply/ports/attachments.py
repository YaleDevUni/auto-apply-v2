from typing import Protocol

from auto_apply.contracts.dto import ResumeAttachment


class AttachmentManager(Protocol):
    """플랫폼 계정에 쌓인 이력서/포트폴리오 첨부파일 관리 (wanted-resume-list-cleanup-backlog).

    `PlatformAdapter`(공고 조회/지원 실행)와 별개 축이다 — 이쪽은 "계정에 뭐가 업로드돼
    있는가"를 다루고, 인증이 필요하다(storage_state 재사용, PlaywrightExecutor 와 동일 계약).
    """

    @property
    def platform(self) -> str: ...

    async def list_attachments(self) -> list[ResumeAttachment]: ...

    async def delete_attachment(self, key: str) -> None: ...


class AttachmentRegistry(Protocol):
    def for_platform(self, platform: str) -> AttachmentManager:
        """등록 안 된 플랫폼이면 PolicyViolation (ports/platform.py 와 같은 계약)."""
        ...
