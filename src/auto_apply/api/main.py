"""FastAPI = Control Plane. 여기서 LLM/Playwright 를 실행하지 않는다 (ARCHITECTURE.md §7)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from temporalio.client import Client

from auto_apply.api.deps import ContainerDep
from auto_apply.api.routers import applications, telegram
from auto_apply.bootstrap import build_container
from auto_apply.config import load_settings
from auto_apply.temporal_config import DATA_CONVERTER


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = load_settings()
    app.state.container = build_container(cfg)
    # workflow 시작/signal/query 는 Temporal SDK 를 직접 쓴다 — 이걸 감싸지 않는 이유는
    # §11.6 참고. 컨테이너(어댑터 조립)와는 별도로 관리한다.
    app.state.temporal_client = await Client.connect(
        cfg.temporal_address, namespace=cfg.temporal_namespace, data_converter=DATA_CONVERTER
    )
    yield


app = FastAPI(title="auto-apply", version="0.1.0", lifespan=lifespan)
app.include_router(applications.router)
app.include_router(telegram.router)


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
