"""native: 전용 프로필 쿠키가 브라우저 재기동·앱 재기동(새 호스트) 뒤에도 남는다 (D6)."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from auto_apply.adapters.browser.playwright_host import PlaywrightBrowserHost
from tests.browsers import real_host

pytestmark = pytest.mark.native


@pytest.fixture
def profile(tmp_path) -> Path:
    return tmp_path / "chrome-profile"


class _CookieSite(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        if self.path == "/login":
            # 로그인 세션처럼 만료가 있는 쿠키 — 세션 쿠키는 재기동에 남지 않는 게 정상이다
            self.send_header("Set-Cookie", "sid=kept; Max-Age=3600; Path=/; HttpOnly")
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(self.headers.get("Cookie", "").encode())

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def cookie_site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CookieSite)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


async def _body(host: PlaywrightBrowserHost, url: str) -> str:
    page = host.vendor_page(await host.page())
    response = await page.goto(url)
    assert response is not None
    return await response.text()


async def test_cookie_survives_restart_and_new_process_host(profile, cookie_site):
    host = real_host(profile)
    try:
        await _body(host, f"{cookie_site}/login")
        assert await _body(host, f"{cookie_site}/check") == "sid=kept"
        await host.close()
        # 같은 호스트 재기동
        assert await _body(host, f"{cookie_site}/check") == "sid=kept"
    finally:
        await host.close()
    # 앱을 새로 띄운 것처럼 새 호스트
    again = real_host(profile)
    try:
        assert await _body(again, f"{cookie_site}/check") == "sid=kept"
    finally:
        await again.close()
