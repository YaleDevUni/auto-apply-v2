"""짐 서버(§A4) — 127.0.0.1 바인딩, 정적 서빙, 요청 기록·분류, 로그인 벽."""

import http.client

import httpx
import pytest

from tests.gym.server import GymServer

HTML = {"Accept": "text/html,application/xhtml+xml"}


@pytest.fixture
def client(gym):
    with httpx.Client(base_url=gym.base_url, follow_redirects=False) as c:
        yield c


def _raw_get(gym, path: str) -> int:
    # httpx 는 ../ 를 정규화해 버린다 — 경로 탈출 시도는 날것 그대로 보내야 시험이 된다
    host, port = gym.address
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        return conn.getresponse().status
    finally:
        conn.close()


def test_binds_loopback_on_ephemeral_port(gym):
    host, port = gym.address
    assert host == "127.0.0.1" and port > 0
    assert gym.base_url == f"http://127.0.0.1:{port}"


def test_serves_site_pages(gym, client):
    r = client.get(gym.manifest.sites["spa_fetch"].entry)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "/api/spa_fetch/submit" in r.text
    assert client.get("/sites/iframe_form/frame.html").status_code == 200
    assert client.get("/sites/done.html").status_code == 200
    assert client.head("/sites/done.html").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/sites/manifest.yaml",
        "/sites/nope/",
        "/sites/../manifest.yaml",
        "/sites/../../pyproject.toml",
        "/sites/spa_fetch/../../gym/server.py",
        "/sites/%2e%2e/manifest.yaml",
        "/",
    ],
)
def test_does_not_serve_outside_sites_or_non_html(gym, path):
    assert _raw_get(gym, path) == 404


def test_traversal_to_html_outside_root_blocked(tmp_path):
    # 확장자 화이트리스트와 별개로 루트 밖 탈출 자체를 막는지 — .html 이면 확장자로는 못 거른다
    (tmp_path / "sites" / "a").mkdir(parents=True)
    (tmp_path / "sites" / "a" / "index.html").write_text("<p>in</p>", encoding="utf-8")
    (tmp_path / "secret.html").write_text("<p>secret</p>", encoding="utf-8")
    with GymServer(root=tmp_path / "sites") as gym:
        assert _raw_get(gym, "/sites/a/") == 200
        assert _raw_get(gym, "/sites/../secret.html") == 404
        assert _raw_get(gym, "/sites/a/../../secret.html") == 404


def test_final_submission_recorded_with_body(gym, client):
    r = client.post("/api/spa_fetch/submit", json={"name": "홍길동"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    [sub] = gym.final_submissions()
    assert (sub.method, sub.path, sub.site, sub.final) == (
        "POST",
        "/api/spa_fetch/submit",
        "spa_fetch",
        True,
    )
    assert "홍길동".encode() in sub.body and sub.content_type == "application/json"
    assert gym.final_submissions("spa_fetch") == [sub]
    assert gym.final_submissions("beacon") == []
    assert gym.unexpected_requests() == []


def test_multipart_body_recorded(gym, client):
    client.post(
        "/api/multipart_form/submit", files={"resume": ("r.pdf", b"%PDF-1.7")}, headers=HTML
    )
    [sub] = gym.final_submissions("multipart_form")
    assert sub.content_type.startswith("multipart/form-data") and b"%PDF-1.7" in sub.body


def test_navigation_submit_redirects_to_done_page(gym, client):
    r = client.post("/api/request_submit/submit", data={"name": "x"}, headers=HTML)
    assert r.status_code == 303 and r.headers["location"] == gym.manifest.done_page
    r = client.post("/api/complete_page/submit", data={"name": "x"}, headers=HTML)
    assert r.headers["location"] == "/sites/complete_page/complete.html"
    assert "지원이 완료되었습니다" in client.get(r.headers["location"]).text
    assert len(gym.final_submissions()) == 2


def test_form_step_navigates_to_next_step(gym, client):
    r = client.post("/api/multi_step_form/step1", data={"name": "x"}, headers=HTML)
    assert r.status_code == 303 and r.headers["location"] == "/sites/multi_step_form/step2.html"
    assert client.get(r.headers["location"]).status_code == 200
    assert gym.final_submissions() == [] and len(gym.intermediate_requests("multi_step_form")) == 1


def test_intermediate_save_is_allowed_not_final(gym, client):
    assert client.post("/api/multi_step/save", json={"step": 1}).status_code == 200
    assert gym.final_submissions() == []
    [save] = gym.intermediate_requests("multi_step")
    assert save.allowed and not save.final
    assert gym.unexpected_requests() == []


def test_unknown_non_get_is_unexpected(gym, client):
    assert client.post("/api/nope/submit").status_code == 404
    assert client.post("/api/spa_fetch/other").status_code == 404
    assert client.put("/api/spa_fetch/submit").status_code == 404  # 메서드도 맞아야 최종 제출
    assert client.post("/sites/spa_fetch/").status_code == 404
    client.get("/sites/spa_fetch/")
    client.get("/favicon.ico")
    assert [(r.method, r.path) for r in gym.unexpected_requests()] == [
        ("POST", "/api/nope/submit"),
        ("POST", "/api/spa_fetch/other"),
        ("PUT", "/api/spa_fetch/submit"),
        ("POST", "/sites/spa_fetch/"),
    ]
    assert gym.final_submissions() == []


def test_every_request_is_recorded_and_clear_resets(gym, client):
    client.get("/sites/done.html?x=1")
    [r] = gym.requests
    assert (r.method, r.path, r.query) == ("GET", "/sites/done.html", "x=1")
    gym.clear()
    assert gym.requests == ()


def test_login_wall_redirects_without_cookie(gym, client):
    site = gym.manifest.sites["login_wall"]
    r = client.get(site.entry)
    assert r.status_code == 303 and r.headers["location"] == site.login.page
    assert client.get(site.login.page).status_code == 200
    assert client.get(site.entry, headers={"Cookie": f"{site.login.cookie}="}).status_code == 303
    assert client.get(site.entry, headers={"Cookie": "bad cookie=="}).status_code == 303
    assert client.get(site.entry, headers={"Cookie": f"{site.login.cookie}=1"}).status_code == 200
    # 다른 사이트는 보호 대상이 아니다
    assert client.get("/sites/beacon/").status_code == 200


def test_login_endpoint_sets_cookie_and_returns_to_entry(gym, client):
    site = gym.manifest.sites["login_wall"]
    r = client.post(site.login.endpoint, data={"username": "u", "password": "p"}, headers=HTML)
    assert r.status_code == 303 and r.headers["location"] == site.entry
    assert site.login.cookie in r.cookies
    assert client.get(site.entry).status_code == 200  # 클라이언트가 쿠키를 들고 다시 온다
    assert gym.final_submissions() == [] and len(gym.intermediate_requests("login_wall")) == 1


def test_wait_for_wakes_on_request_and_times_out(gym, client):
    import threading

    threading.Timer(0.1, lambda: client.post("/api/beacon/submit", content=b"x")).start()
    assert gym.wait_for(lambda: gym.final_submissions("beacon"), timeout=5)
    assert not gym.wait_for(lambda: gym.final_submissions("spa_fetch"), timeout=0.1)


def test_stop_releases_port():
    server = GymServer().start()
    host, port = server.address
    server.stop()
    with pytest.raises(OSError):
        http.client.HTTPConnection(host, port, timeout=1).connect()
    with pytest.raises(RuntimeError):
        server.base_url
