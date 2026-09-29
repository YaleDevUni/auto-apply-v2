"""로컬 보안 (§A10) — Host·Origin·설치별 토큰, `GET /api/session` (T1.2)."""

import os
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

from auto_apply.api.main import create_app
from auto_apply.api.security import TOKEN_HEADER
from auto_apply.bootstrap import ensure_session_token
from auto_apply.config import Settings

LOCAL = "http://127.0.0.1:8765"
WEB = "http://localhost:5173"
PROFILE = {"name": "홍길동"}


@pytest.fixture
def cfg(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        storage="memory",
        llm_provider="stub",
        guide_source="static",
        repository="memory",
        web_cors_origin=WEB,
        document_max_bytes=4096,
    )


@pytest.fixture
def client(cfg):
    with TestClient(create_app(cfg), base_url=LOCAL) as c:
        yield c


@pytest.fixture
def token(client) -> str:
    return client.get("/api/session").json()["token"]


def _code(res) -> str:
    return res.json()["error"]["code"]


def test_session_returns_install_token(client, cfg):
    res = client.get("/api/session", headers={"Origin": WEB})
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["access-control-allow-origin"] == WEB
    body = res.json()
    assert body == {"token": cfg.session_token_path.read_text().strip(), "header": TOKEN_HEADER}
    assert len(body["token"]) >= 32


def test_mutation_without_token_is_403(client):
    res = client.put("/api/profile", json=PROFILE)
    assert res.status_code == 403
    assert _code(res) == "invalid_token"
    assert client.get("/api/profile").status_code == 404  # 아무것도 쓰이지 않았다


def test_mutation_with_wrong_token_is_403(client, token):
    res = client.put("/api/profile", json=PROFILE, headers={TOKEN_HEADER: token[:-1] + "x"})
    assert (res.status_code, _code(res)) == (403, "invalid_token")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/experiences"),
        ("PUT", "/api/experiences/x"),
        ("DELETE", "/api/experiences/x"),
        ("POST", "/api/answers"),
        ("DELETE", "/api/answers/x"),
        ("POST", "/api/documents"),
        ("DELETE", "/api/documents/x"),
        ("POST", "/api/profile/drafts"),
        ("POST", "/api/profile/drafts/v2-import"),
        ("POST", "/api/profile/drafts/upload"),
        ("PUT", "/api/profile/drafts/x"),
        ("DELETE", "/api/profile/drafts/x"),
        ("POST", "/api/profile/drafts/x/confirm"),
    ],
)
def test_every_mutation_requires_token(client, method, path):
    res = client.request(method, path, json={})
    assert (res.status_code, _code(res)) == (403, "invalid_token")


def test_upload_without_token_rejected_before_reading_body(client):
    res = client.post("/api/documents", files={"file": ("a.pdf", b"%PDF-" * 10, "application/pdf")})
    assert (res.status_code, _code(res)) == (403, "invalid_token")


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "http://localhost:3000", "null", "http://127.0.0.1:5173"]
)
def test_wrong_origin_is_403_even_with_token(client, token, origin):
    res = client.put("/api/profile", json=PROFILE, headers={TOKEN_HEADER: token, "Origin": origin})
    assert (res.status_code, _code(res)) == (403, "origin_not_allowed")
    assert client.get("/api/profile").status_code == 404


def test_wrong_origin_cannot_fetch_session(client):
    res = client.get("/api/session", headers={"Origin": "https://evil.example"})
    assert (res.status_code, _code(res)) == (403, "origin_not_allowed")
    assert "token" not in res.json()
    # CORS 로도 막힌다 — 타 출처에 허용 헤더를 주지 않는다
    assert "access-control-allow-origin" not in res.headers


def test_cors_preflight_only_for_web_origin(client):
    def preflight(origin: str) -> httpx.Response:
        return client.options(
            "/api/profile",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": f"content-type,{TOKEN_HEADER.lower()}",
            },
        )

    ok = preflight(WEB)
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == WEB
    assert TOKEN_HEADER.lower() in ok.headers["access-control-allow-headers"].lower()
    bad = preflight("https://evil.example")
    assert bad.status_code == 400
    assert "access-control-allow-origin" not in bad.headers


@pytest.mark.parametrize("origin", [WEB, LOCAL])
def test_allowed_origins_with_token_pass(client, token, origin):
    """웹 콘솔 개발 서버 출처, 그리고 이 서버와 같은 출처(나중에 웹을 같이 서빙할 때)."""
    res = client.put("/api/profile", json=PROFILE, headers={TOKEN_HEADER: token, "Origin": origin})
    assert res.status_code == 200, res.text


def test_missing_origin_with_token_passes(client, token):
    """브라우저 밖 로컬 프로세스는 Origin 이 없다 — 토큰만으로 통과."""
    assert client.put("/api/profile", json=PROFILE, headers={TOKEN_HEADER: token}).is_success


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "192.168.0.10:8765"])
def test_non_local_host_is_403(cfg, host):
    """DNS 리바인딩: 공격 도메인이 127.0.0.1 로 풀려도 Host 헤더는 그 도메인이다."""
    with TestClient(create_app(cfg), base_url=f"http://{host}") as c:
        for res in (c.get("/api/session"), c.get("/api/profile"), c.get("/health")):
            assert (res.status_code, _code(res)) == (403, "host_not_allowed")


def test_error_responses_carry_cors_headers_for_web(client):
    res = client.put("/api/profile", json=PROFILE, headers={"Origin": WEB})
    assert res.status_code == 403
    assert res.headers["access-control-allow-origin"] == WEB


# ── 토큰 파일 ────────────────────────────────────────────────────────────────


def test_token_created_once_and_reused(tmp_path):
    path = tmp_path / "nested" / "session_token"
    first = ensure_session_token(path)
    assert path.read_text() == first
    assert ensure_session_token(path) == first
    assert list(path.parent.iterdir()) == [path]  # 임시 파일이 남지 않는다


@pytest.mark.skipif(os.name == "nt", reason="POSIX 권한 비트")
def test_token_file_is_owner_only(tmp_path):
    path = tmp_path / "session_token"
    ensure_session_token(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o644)
    ensure_session_token(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_short_or_empty_token_is_regenerated(tmp_path):
    path = tmp_path / "session_token"
    path.write_text("weak")
    token = ensure_session_token(path)
    assert token != "weak" and len(token) >= 32


def test_token_survives_restart(cfg):
    tokens = []
    for _ in range(2):
        with TestClient(create_app(cfg), base_url=LOCAL) as c:
            tokens.append(c.get("/api/session").json()["token"])
    assert tokens[0] == tokens[1]


# ── 개발 모드 / 설치본 (§A10) ────────────────────────────────────────────────


@pytest.fixture
def installed(monkeypatch, tmp_path):
    """설치본처럼: cwd 가 이 저장소가 아니고 AUTO_APPLY_DEV 도 없다."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTO_APPLY_DEV", raising=False)


def _cfg(tmp_path, **kw) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        storage="memory",
        llm_provider="stub",
        guide_source="static",
        repository="memory",
        **kw,
    )


def test_installed_has_no_default_web_origin(installed, tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.web_cors_origin is None
    with TestClient(create_app(cfg), base_url=LOCAL) as c:
        token = c.get("/api/session").json()["token"]
        res = c.put("/api/profile", json=PROFILE, headers={TOKEN_HEADER: token, "Origin": WEB})
        assert (res.status_code, _code(res)) == (403, "origin_not_allowed")
        assert "access-control-allow-origin" not in res.headers
        res = c.get("/api/session", headers={"Origin": WEB})
        assert (res.status_code, _code(res)) == (403, "origin_not_allowed")
        # 같은 출처(나중에 웹을 이 서버가 서빙할 때)는 통과
        res = c.put("/api/profile", json=PROFILE, headers={TOKEN_HEADER: token, "Origin": LOCAL})
        assert res.status_code == 200


def test_installed_can_opt_in_web_origin(installed, tmp_path):
    cfg = _cfg(tmp_path, web_cors_origin=WEB)
    with TestClient(create_app(cfg), base_url=LOCAL) as c:
        assert c.get("/api/session", headers={"Origin": WEB}).status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_installed_hides_api_docs(installed, tmp_path, path):
    with TestClient(create_app(_cfg(tmp_path)), base_url=LOCAL) as c:
        assert c.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_dev_mode_serves_api_docs_and_default_origin(monkeypatch, tmp_path, path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTO_APPLY_DEV", "1")
    cfg = _cfg(tmp_path)
    assert cfg.web_cors_origin == WEB
    with TestClient(create_app(cfg), base_url=LOCAL) as c:
        assert c.get(path).status_code == 200
