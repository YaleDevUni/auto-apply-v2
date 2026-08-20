"""AttachmentManager port 의 원티드 구현 (wanted-resume-list-cleanup-backlog).

`WantedPlatformAdapter`(공고 조회)와 달리 인증이 필요하다 — Recipe 실행과 같은 storage_state
(`scripts/save_auth_state.py` 로 사람이 만든 `var/auth/wanted.json`, `PlaywrightExecutor` 와
동일 계약)를 쿠키로만 재사용한다. 브라우저를 새로 띄우지 않는다 — 실측(2026-08-20,
agent-browser 라이브 탐색): 이 API(`/api/chaos/resumes/v1`)는 쿠키 인증만으로 동작하고
Authorization 헤더/localStorage 토큰이 필요 없다(httpx 로 쿠키만 실어 직접 검증함).

`update_time` 응답은 타임존 표기가 없는 KST 벽시계 값이다(실측 — 요청 시각의 UTC/KST를
비교해 확인) — `Asia/Seoul` 을 명시로 붙여야 `domain/resume_cleanup.select_deletable` 의
tz-aware `now` 뺄셈이 안전하다.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from auto_apply.adapters._wanted_auth import wanted_cookie_client
from auto_apply.contracts.dto import ResumeAttachment

_BASE_URL = "https://www.wanted.co.kr/api/chaos/resumes/v1"
_PAGE_SIZE = 50
_KST = ZoneInfo("Asia/Seoul")


class WantedAttachmentManager:
    def __init__(
        self, *, auth_dir: Path, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._auth_dir = auth_dir
        self._transport = transport  # 테스트에서만 httpx.MockTransport 로 주입한다

    @property
    def platform(self) -> str:
        return "wanted"

    def _client(self) -> httpx.AsyncClient:
        return wanted_cookie_client(self._auth_dir, transport=self._transport)

    async def list_attachments(self) -> list[ResumeAttachment]:
        out: list[ResumeAttachment] = []
        async with self._client() as client:
            offset = 0
            while True:
                resp = await client.get(
                    _BASE_URL,
                    params={"offset": offset, "limit": _PAGE_SIZE, "sort_by_completion": 0},
                )
                resp.raise_for_status()
                body = resp.json()
                for row in body.get("data", []):
                    out.append(
                        ResumeAttachment(
                            key=row["key"],
                            title=row["title"],
                            content_type=row["content_type"],
                            updated_at=datetime.fromisoformat(row["update_time"]).replace(
                                tzinfo=_KST
                            ),
                        )
                    )
                if not (body.get("links") or {}).get("next"):
                    break
                offset += _PAGE_SIZE
        return out

    async def delete_attachment(self, key: str) -> None:
        async with self._client() as client:
            resp = await client.delete(f"{_BASE_URL}/{key}")
            resp.raise_for_status()
