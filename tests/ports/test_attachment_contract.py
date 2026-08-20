"""AttachmentManager contract test (wanted-resume-list-cleanup-backlog).

WantedAttachmentManager 는 `httpx.MockTransport` 로 오프라인 검증한다(`test_platform_contract.py`
와 같은 패턴) — 인증은 storage_state 파일이 필요하므로 tmp_path 에 최소 쿠키 하나만 있는
가짜 파일을 만든다.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from auto_apply.adapters.attachments.fixture import FixtureAttachmentManager
from auto_apply.adapters.attachments.registry import StaticAttachmentRegistry
from auto_apply.adapters.attachments.wanted import WantedAttachmentManager
from auto_apply.contracts.dto import ResumeAttachment
from auto_apply.domain.errors import AuthRequired, PolicyViolation

LIST_PAGE_1 = {
    "links": {"next": "/api/chaos/resumes/v1?offset=1&limit=1"},
    "data": [
        {
            "key": "k1",
            "title": "res_2942bf8c75c249e7.pdf",
            "content_type": "application/pdf",
            "update_time": "2026-08-20T14:05:59",
        }
    ],
}
LIST_PAGE_2 = {
    "links": {"next": None},
    "data": [
        {
            "key": "k2",
            "title": "박예일_포트폴리오_데브옵스.pdf",
            "content_type": "application/pdf",
            "update_time": "2026-08-18T10:00:00",
        }
    ],
}


def _auth_dir(tmp_path: Path) -> Path:
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    state = {
        "cookies": [{"name": "session", "value": "x", "domain": ".wanted.co.kr", "path": "/"}],
        "origins": [],
    }
    (auth_dir / "wanted.json").write_text(json.dumps(state))
    return auth_dir


def _wanted_manager(tmp_path: Path, handler) -> WantedAttachmentManager:
    return WantedAttachmentManager(
        auth_dir=_auth_dir(tmp_path), transport=httpx.MockTransport(handler)
    )


class TestFixtureAttachmentManager:
    async def test_list_and_delete_round_trip(self):
        manager = FixtureAttachmentManager(
            [
                ResumeAttachment(
                    key="k1",
                    title="a.pdf",
                    content_type="application/pdf",
                    updated_at=datetime.now(UTC),
                )
            ]
        )
        assert len(await manager.list_attachments()) == 1
        await manager.delete_attachment("k1")
        assert await manager.list_attachments() == []

    async def test_delete_missing_key_is_idempotent(self):
        manager = FixtureAttachmentManager()
        await manager.delete_attachment("nope")  # 조용히 성공해야 한다


class TestWantedAttachmentManager:
    async def test_list_attachments_paginates_and_parses(self, tmp_path: Path):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            page = LIST_PAGE_1 if "offset=0" in str(request.url) else LIST_PAGE_2
            return httpx.Response(200, json=page)

        manager = _wanted_manager(tmp_path, handler)
        attachments = await manager.list_attachments()

        assert [a.key for a in attachments] == ["k1", "k2"]
        assert attachments[0].updated_at.tzinfo is not None  # KST 명시가 붙어야 한다
        assert len(calls) == 2  # links.next 를 따라 두 번째 페이지까지 갔다

    async def test_list_attachments_raises_auth_required_without_state_file(self, tmp_path: Path):
        manager = WantedAttachmentManager(auth_dir=tmp_path / "no-such-dir")
        with pytest.raises(AuthRequired):
            await manager.list_attachments()

    async def test_delete_attachment_calls_delete_endpoint(self, tmp_path: Path):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            return httpx.Response(200, json={})

        manager = _wanted_manager(tmp_path, handler)
        await manager.delete_attachment("AwEHCwwABQdLBk9e")

        assert seen["method"] == "DELETE"
        assert seen["url"].endswith("/AwEHCwwABQdLBk9e")


class TestStaticAttachmentRegistry:
    def test_for_platform_dispatches(self):
        wanted = WantedAttachmentManager(auth_dir=Path("/nonexistent"))
        registry = StaticAttachmentRegistry([wanted])
        assert registry.for_platform("wanted") is wanted

    def test_for_platform_unregistered_raises(self):
        registry = StaticAttachmentRegistry([])
        with pytest.raises(PolicyViolation):
            registry.for_platform("wanted")
