"""짐 서버의 HTTP 처리 — 요청을 기록하고, 매니페스트대로 응답하고, 사이트 파일만 서빙한다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from tests.gym.server import GymServer

_CONTENT_TYPES = {".html": "text/html; charset=utf-8"}  # 사이트는 인라인 스크립트만 쓴다


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: bytes
    site: str | None
    final: bool
    allowed: bool

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")


class GymHandler(BaseHTTPRequestHandler):
    server_version = "gym"

    @property
    def gym(self) -> GymServer:
        return self.server.gym  # type: ignore[attr-defined,no-any-return]

    def log_message(self, format: str, *args: object) -> None:  # 테스트 출력 오염 방지
        return

    def do_GET(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def _handle(self) -> None:
        split = urlsplit(self.path)
        path, method = split.path, self.command
        body = self._read_body()
        manifest = self.gym.manifest
        hit = manifest.endpoint(method, path)
        self.gym.record(
            RecordedRequest(
                method=method,
                path=path,
                query=split.query,
                headers={k.lower(): v for k, v in self.headers.items()},
                body=body,
                site=manifest.site_of(path),
                final=bool(hit and hit[1]),
                allowed=bool(hit and not hit[1]),
            )
        )
        if hit is not None:
            self._api(path, hit[0].redirect)
        elif method in ("GET", "HEAD") and path.startswith("/sites/"):
            self._static(path)
        else:
            self._send(404, b'{"error":"not found"}', "application/json")

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if length is None:
            if self.headers.get("Transfer-Encoding"):
                # 청크 본문은 읽지 않는다 — 연결을 버려 다음 요청이 꼬이지 않게
                self.close_connection = True
            return b""
        return self.rfile.read(int(length))

    def _api(self, path: str, redirect: str | None) -> None:
        site = self.gym.manifest.sites.get(self.gym.manifest.site_of(path) or "")
        if site is not None and site.login is not None and path == site.login.endpoint:
            # 자격증명은 검사하지 않는다 — 짐은 "로그인 벽이 있다"만 흉내 낸다
            self._redirect(site.entry, f"{site.login.cookie}=1; Path=/; SameSite=Lax")
        elif "text/html" in self.headers.get("Accept", ""):
            # 브라우저 탐색(form 제출)은 완료 페이지로 — 사후 감지(L5)가 볼 화면
            self._redirect(redirect or self.gym.manifest.done_page)
        else:
            self._send(200, json.dumps({"ok": True}).encode(), "application/json")

    def _static(self, path: str) -> None:
        guard = self._login_redirect(path)
        if guard is not None:
            self._redirect(guard)
            return
        target = (self.gym.root / path.removeprefix("/sites/")).resolve()
        if not target.is_relative_to(self.gym.root):  # 경로 탈출(../) 차단
            self._send(404, b"not found", "text/plain")
            return
        if target.is_dir():
            target = target / "index.html"
        ctype = _CONTENT_TYPES.get(target.suffix)
        if ctype is None or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        self._send(200, target.read_bytes(), ctype)

    def _login_redirect(self, path: str) -> str | None:
        for site in self.gym.manifest.sites.values():
            login = site.login
            if login is None or not path.startswith(login.prefix) or path == login.page:
                continue
            try:
                jar = SimpleCookie(self.headers.get("Cookie", ""))
            except CookieError:
                jar = SimpleCookie()
            if not (login.cookie in jar and jar[login.cookie].value):
                return login.page
        return None

    def _redirect(self, location: str, set_cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
