"""FastAPI = Control Plane. 여기서 LLM/Playwright 를 실행하지 않는다 (ARCHITECTURE.md §7)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from auto_apply.api.deps import ContainerDep
from auto_apply.bootstrap import build_container
from auto_apply.config import load_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.container = build_container(load_settings())
    yield


app = FastAPI(title="auto-apply", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz(c: ContainerDep) -> dict[str, Any]:
    """어떤 어댑터 조합으로 떠 있는지 그대로 노출한다. 디버깅 1순위 정보."""
    return {
        "status": "ok",
        "env": c.settings.app_env,
        "adapters": {
            "llm": c.settings.llm_provider,
            "storage": c.settings.storage,
            "notifier": c.settings.notifier,
            "executor": c.settings.executor,
            "resume_engine": c.settings.resume_engine,
        },
        "dry_run_only": c.settings.dry_run_only,
    }
