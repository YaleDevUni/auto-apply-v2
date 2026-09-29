"""로컬 보안 (§A10) — 127.0.0.1 에만 뜨는 서버라도 브라우저가 대신 부르는 요청은 막아야 한다.

세 겹:
1. `Host` 가 로컬 이름(127.0.0.1·localhost)이 아니면 거부 — DNS 리바인딩(공격 페이지의 도메인을
   127.0.0.1 로 돌려 "같은 출처"가 되는 것)을 막는다. 리바인딩되면 CORS 도 Origin 검사도 못 막는다.
2. `Origin` 이 있으면 허용 출처(`WEB_CORS_ORIGIN` 또는 이 서버와 같은 출처)여야 한다. 브라우저는
   변경 요청에 항상 Origin 을 붙이므로 타 사이트의 CSRF 는 여기서 걸린다. Origin 이 없는 요청은
   브라우저가 아닌 로컬 프로세스다 — 그쪽은 3 이 막는다.
3. 변경 요청(POST·PUT·PATCH·DELETE)은 설치별 토큰 헤더가 맞아야 한다. 토큰은 데이터 디렉터리
   파일(0600)과 `GET /api/session` 으로만 얻는다 — 후자는 1·2 를 통과한 로컬 웹 콘솔만 부를 수 있다.
"""

import secrets

from fastapi import APIRouter, Response
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from auto_apply.api.deps import ContainerDep
from auto_apply.api.errors import error_response
from auto_apply.contracts._base import Frozen

TOKEN_HEADER = "X-Auto-Apply-Token"
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost"})
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _hostname(host: str) -> str:
    """`Host` 헤더에서 포트를 뗀 이름. IPv6 리터럴(`[::1]:8765`)은 로컬 목록에 없어 거부된다."""
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class LocalSecurityMiddleware:
    """CORS 미들웨어 **안쪽**에 둔다 — 거부 응답에도 CORS 헤더가 붙어야 웹이 에러 본문을 읽는다."""

    def __init__(self, app: ASGIApp, *, web_origin: str | None) -> None:
        self.app = app
        self._web_origin = web_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        denial = self._check(scope)
        if denial is not None:
            code, message = denial
            await error_response(403, code, message)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _check(self, scope: Scope) -> tuple[str, str] | None:
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if _hostname(host).lower() not in LOCAL_HOSTNAMES:
            return "host_not_allowed", "로컬 주소(127.0.0.1·localhost)로만 접속할 수 있다"
        origin = headers.get("origin")
        if origin is not None and origin not in {self._web_origin, f"{scope['scheme']}://{host}"}:
            return "origin_not_allowed", "허용되지 않은 출처의 요청이다"
        if scope["method"] in _MUTATING:
            expected: str = scope["app"].state.container.session_token
            given = headers.get(TOKEN_HEADER, "")
            if not secrets.compare_digest(given.encode(), expected.encode()):
                return "invalid_token", f"{TOKEN_HEADER} 헤더가 없거나 틀렸다"
        return None


class SessionInfo(Frozen):
    token: str
    header: str = TOKEN_HEADER


session_router = APIRouter(prefix="/api", tags=["session"])


@session_router.get("/session")
async def get_session(c: ContainerDep, response: Response) -> SessionInfo:
    """웹 콘솔이 변경 요청에 붙일 토큰. 타 출처는 미들웨어(Origin)와 CORS 가 둘 다 막는다."""
    response.headers["Cache-Control"] = "no-store"
    return SessionInfo(token=c.session_token)
