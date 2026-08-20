"""AttachmentManager port 의 오프라인 대역 (§11.1 "구현 2개" 원칙, WantedAttachmentManager 짝).

seed 로 넘긴 목록을 메모리에서 그대로 관리한다 — 네트워크도, storage_state 도 필요 없다.
"""

from auto_apply.contracts.dto import ResumeAttachment


class FixtureAttachmentManager:
    def __init__(self, attachments: list[ResumeAttachment] | None = None) -> None:
        self._attachments = {a.key: a for a in (attachments or [])}

    @property
    def platform(self) -> str:
        return "fixture"

    async def list_attachments(self) -> list[ResumeAttachment]:
        return list(self._attachments.values())

    async def delete_attachment(self, key: str) -> None:
        self._attachments.pop(key, None)  # 이미 없는 키를 지워도 조용히 성공 — DELETE 는 멱등
