"""FastAPI 진입점. v3 라우터·/health 는 T0.3 에서 다시 짠다 (§A1, §A10)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auto_apply.api.deps import ContainerDep
from auto_apply.bootstrap import build_container
from auto_apply.config import load_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.container = build_container(load_settings())
    yield


app = FastAPI(title="auto-apply", version="0.1.0", lifespan=lifespan)

# 웹 콘솔(Vite dev 서버)에서의 cross-origin 호출 허용 — 설정된 origin 하나만 명시적으로 연다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[load_settings().web_cors_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz(c: ContainerDep) -> dict[str, Any]:
    """어떤 어댑터 조합으로 떠 있는지 그대로 노출한다. 디버깅 1순위 정보."""
    return {
        "status": "ok",
        "env": c.settings.app_env,
        "adapters": {
            "llm": c.settings.llm_provider,
            "storage": c.settings.storage,
            "repository": c.settings.repository,
            "resume_engine": c.settings.resume_engine,
        },
        "dry_run_only": c.settings.dry_run_only,
    }
