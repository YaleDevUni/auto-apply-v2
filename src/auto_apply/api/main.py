"""FastAPI 앱 조립. 기동 순서는 lifespan 하나에 모은다 (§A1).

`auto-apply` 콘솔 스크립트(`__main__.py`)와 `uvicorn auto_apply.api.main:app` 이 같은 경로로 뜬다 —
어느 쪽으로 띄워도 데이터 디렉터리 준비·마이그레이션·JobRunner 기동·브라우저 정리가 빠지지 않게.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Route

from auto_apply import __version__
from auto_apply.api import document_routes, draft_routes, profile_routes
from auto_apply.api.deps import ContainerDep
from auto_apply.api.errors import install_error_handlers
from auto_apply.api.mcp import MCP_PATH, McpEndpoint
from auto_apply.api.security import TOKEN_HEADER, LocalSecurityMiddleware, session_router
from auto_apply.bootstrap import build_container, prepare_data_dir
from auto_apply.config import Settings, load_settings

# `make api`(uvicorn --port 8000)의 포트. uvicorn 은 lifespan 을 끝낸 뒤에 바인드하고 고른 포트를
# 앱에 알려주지 않는다 — 그래서 모듈 수준 앱은 이 값을 믿는다(다른 포트로 띄우면 MCP URL 이
# 틀려 CLI 런타임은 MCP 연결 점검에서 run 을 멈춘다, 닫힌 쪽). 콘솔 스크립트는 바인드한 실제
# 포트를 넘긴다.
DEV_API_PORT = 8000


def create_app(
    settings: Settings | None = None, *, prepare: bool = True, port: int | None = None
) -> FastAPI:
    """`prepare=False` 는 호출자가 이미 `prepare_data_dir` 를 끝낸 경우(콘솔 스크립트).

    `port` 는 앱이 실제로 받는 포트 — 금지 출처(§A5)와 MCP URL(§A6)에 같은 값으로 들어간다.
    None 은 포트가 없는 테스트 전송(ASGI) 전용이다.
    """
    cfg = settings or load_settings()
    mcp = McpEndpoint()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if prepare:
            prepare_data_dir(cfg)
        container = build_container(cfg, port=port)
        app.state.container = container
        await container.runner.start()
        try:
            # MCP 요청이 먼저 끊겨야 러너 정지 뒤 도구 호출이 브라우저를 다시 건드리지 않는다
            async with mcp.running():
                yield
        finally:
            # 러너가 먼저 서야 브라우저를 쓰던 작업이 닫힌 브라우저를 다시 띄우지 않는다.
            await container.runner.stop()
            await container.browser.close()

    # API 문서는 개발 모드에서만 — 설치본은 쓰는 사람이 없고, 열어두면 공격 표면 지도만 된다 (§A10).
    docs = cfg.dev_mode
    app = FastAPI(
        title="auto-apply",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    install_error_handlers(app)
    # 나중에 추가한 미들웨어가 바깥이다: CORS(바깥) → 로컬 보안(안쪽). 프리플라이트는 CORS 가
    # 답하고, 보안 미들웨어의 403 에도 CORS 헤더가 붙어 웹이 에러 본문을 읽는다 (§A10).
    app.add_middleware(LocalSecurityMiddleware, web_origin=cfg.web_cors_origin, mcp_path=MCP_PATH)
    # 웹 콘솔 개발 서버(Vite)에서의 cross-origin 호출 — 설정된 origin 하나만 연다.
    if cfg.web_cors_origin is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[cfg.web_cors_origin],
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", TOKEN_HEADER],
        )
    app.include_router(session_router)
    app.include_router(profile_routes.router)
    app.include_router(document_routes.router)
    app.include_router(draft_routes.router)
    # CLI 런타임의 도구 통로(§A5) — run 토큰으로만 열린다. ASGI 앱 그대로라 메서드는 MCP 가 가린다
    app.router.routes.append(Route(MCP_PATH, endpoint=mcp, include_in_schema=False))

    @app.get("/health")
    async def health(c: ContainerDep) -> dict[str, Any]:
        """어떤 어댑터 조합·데이터 디렉터리로 떠 있는지 그대로 노출한다. 디버깅 1순위 정보."""
        return {
            "status": "ok",
            "version": __version__,
            "env": c.settings.app_env,
            "data_dir": str(c.settings.data_dir),
            "adapters": {
                "llm": c.settings.llm_provider,
                "storage": c.settings.storage,
                "repository": c.settings.repository,
                "resume_engine": c.settings.resume_engine,
            },
            "runner": {"running": c.runner.running},
            "dry_run_only": c.settings.dry_run_only,
            # 금지 출처·MCP URL 이 이 값에서 나온다 — `--port 0` 이면 고른 포트
            "server_origin": c.server_origin,
        }

    return app


# `uvicorn auto_apply.api.main:app` (make api) 용. 콘솔 스크립트는 create_app 을 직접 부른다.
app = create_app(port=DEV_API_PORT)
