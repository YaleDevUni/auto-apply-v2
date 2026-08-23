"""FastAPI = Control Plane. 여기서 LLM/Playwright 를 실행하지 않는다 (ARCHITECTURE.md §7)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from temporalio.client import Client

from auto_apply.api.deps import ContainerDep
from auto_apply.api.routers import applications, recipes, telegram
from auto_apply.bootstrap import Container, build_container
from auto_apply.config import load_settings
from auto_apply.contracts.dto import NotifyEvent
from auto_apply.process_alerts import notify_safely
from auto_apply.telegram.bridge import inbound_failure_message
from auto_apply.temporal_config import DATA_CONVERTER

log = structlog.get_logger(__name__)


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
app.include_router(recipes.router)
app.include_router(telegram.router)


@app.exception_handler(Exception)
async def alert_on_unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """미처리 예외를 500 으로 돌려주기 전에 사람에게 알린다.

    이 API 는 컨트롤 플레인이라 호출자가 늘 사람인 건 아니다 — `POST /telegram/webhook` 은
    텔레그램 서버가 부르므로 여기서 500 이 나면 버튼을 누른 사람에게는 그냥 무응답으로 보이고
    (`telegram/listener.py` 의 dispatch 실패와 같은 증상), 로그를 열지 않는 한 아무도 모른다.
    그래서 응답 코드와 별개로 알림을 한 번 보낸다.

    웹훅만 200 을 돌려준다 — 텔레그램은 5xx 응답을 받으면 같은 update 를 재전송한다
    (https://core.telegram.org/bots/api#getting-updates). 계속 실패하는 update(예: 파싱
    자체가 안 되는 body) 하나가 재전송될 때마다 이 예외 처리기가 다시 타서 같은 사람에게
    같은 `API_ERROR` 알림이 반복해서 쏟아진다(/code-review finding #5) — 이미 이 알림이
    "다시 시도해주세요"라고 안내하므로, 텔레그램에게는 수신 확인만 해주고 재전송을 유도하지
    않는다(롱폴링 경로가 실패해도 offset 을 넘겨 재수신하지 않는 것과 같은 처리). 웹훅이
    아닌 일반 API 호출은 여전히 500 을 돌려줘 호출자가 실패를 알 수 있게 둔다.
    """
    container = cast(Container | None, getattr(request.app.state, "container", None))
    log.exception("api.unhandled_error", path=request.url.path, method=request.method)
    is_webhook = request.url.path.startswith("/telegram/")
    if container is not None:
        message = (
            inbound_failure_message(exc)
            if is_webhook
            else f"API 요청 처리 실패: {request.method} {request.url.path}\n"
            f"{type(exc).__name__}: {exc}"
        )
        await notify_safely(container.notifier, NotifyEvent(kind="API_ERROR", message=message))
    status_code = 200 if is_webhook else 500
    return JSONResponse(status_code=status_code, content={"detail": "internal server error"})


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
