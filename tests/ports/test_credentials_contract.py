"""CredentialSource contract test (ARCHITECTURE.md §11.2, §2.4b).

ports/credentials.py 의 계약: 키(회사명)로 조회, 없으면 None. `JsonQueueCredentialSource`는
`FactSource`/`GuideSource`와 같은 패턴(캐시 없이 매번 읽기) — 이 테스트는 실제 tmp_path 파일
I/O 로 그걸 확인한다. 실제 외부 프로세스가 없어 integration 마킹이 필요 없다.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from auto_apply.adapters.credentials.json_queue import JsonQueueCredentialSource
from auto_apply.adapters.credentials.static import StaticCredentialSource
from auto_apply.ports.credentials import Credential, CredentialSource


@pytest.fixture(params=["static", "json"])
def source(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[CredentialSource]:
    if request.param == "static":
        yield StaticCredentialSource({"acme": Credential(username="u1", password="p1")})
        return
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"acme": {"username": "u1", "password": "p1"}}))
    yield JsonQueueCredentialSource(path)


async def test_get_returns_credential_for_known_key(source: CredentialSource) -> None:
    cred = await source.get("acme")
    assert cred == Credential(username="u1", password="p1")


async def test_get_returns_none_for_unknown_key(source: CredentialSource) -> None:
    assert await source.get("no-such-company") is None


async def test_json_source_is_case_and_whitespace_insensitive(tmp_path: Path) -> None:
    """정규화(casefold+strip)는 JSON 큐 어댑터의 구현 디테일이다 — 사람이 손으로 회사명을

    입력하는 파일이라 표기 흔들림을 흡수하려는 것이지, 포트가 모든 구현에 강제하는 계약은
    아니다(그래서 공유 contract test 가 아니라 여기 json 전용으로 둔다)."""
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"acme": {"username": "u1", "password": "p1"}}))
    source = JsonQueueCredentialSource(path)
    assert await source.get("  ACME  ") == Credential(username="u1", password="p1")


async def test_json_source_reads_file_fresh_every_call(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"acme": {"username": "u1", "password": "p1"}}))
    source = JsonQueueCredentialSource(path)
    assert await source.get("acme") is not None

    path.write_text(json.dumps({}))
    assert await source.get("acme") is None


async def test_json_source_missing_file_returns_none(tmp_path: Path) -> None:
    source = JsonQueueCredentialSource(tmp_path / "missing.json")
    assert await source.get("acme") is None


async def test_json_source_skips_key_that_looks_like_a_known_platform(tmp_path: Path) -> None:
    """'회사명이 플랫폼명이 되면 안 된다' — job.company 대신 job.platform 을 잘못 넘긴

    데이터 버그를 조기에 잡는다(사용자 명시 요구사항)."""
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "wanted": {"username": "u1", "password": "p1"},
                "greenhouse.io": {"username": "u2", "password": "p2"},
                "acme": {"username": "u3", "password": "p3"},
            }
        )
    )
    source = JsonQueueCredentialSource(path)
    assert await source.get("wanted") is None
    assert await source.get("greenhouse.io") is None
    assert await source.get("acme") == Credential(username="u3", password="p3")
