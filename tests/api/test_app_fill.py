"""앱 기동 → fill job 투입 → 앱의 JobRunner 가 소비 → AWAITING_APPROVAL (§A1·§A6·§A9, T3.7).

bootstrap 이 조립한 그대로(러너·핸들러·도구 상자·run 기록)를 쓰고, 브라우저와 에이전트만 대역이다
— 대역 브라우저는 등록한 가짜 사이트만 연다(외부 접속 없음).
"""

import asyncio

from fastapi.testclient import TestClient

from auto_apply.adapters.browser.fake import FakeBrowserHost
from auto_apply.adapters.browser.fake_guard import FakeGuardedPageDriver
from auto_apply.api import main as api_main
from auto_apply.api.main import create_app
from auto_apply.bootstrap import BrowserPair
from auto_apply.contracts.agent import AgentLimits, AgentOutcome, AgentTool
from auto_apply.contracts.jobs import JobKind
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.enums import RunStatus
from auto_apply.ports.agent import AgentRuntime, CallTool
from auto_apply.services.run_artifacts import RunArtifacts
from tests.api.mcp_kit import settings
from tests.runner.fill_kit import FINAL, FORM, fill_form_script, scripted, sites

LOCAL = "http://127.0.0.1:8765"
PORT = 8765


class RecordingRuntime:
    """넘겨받은 run_id 를 적어 두는 껍데기 — CLI 런타임은 이 이름으로 runs/<run_id>/ 를 쓴다."""

    def __init__(self, inner: AgentRuntime) -> None:
        self._inner = inner
        self.run_ids: list[str | None] = []

    async def run(
        self,
        system_prompt: str,
        tools: list[AgentTool],
        call_tool: CallTool,
        *,
        limits: AgentLimits,
        run_id: str | None = None,
    ) -> AgentOutcome:
        self.run_ids.append(run_id)
        return await self._inner.run(system_prompt, tools, call_tool, limits=limits, run_id=run_id)


async def _until(check) -> None:
    for _ in range(500):  # 10초
        if await check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("fill job 이 끝나지 않았다")


def test_app_runner_drives_fill_job_to_awaiting_approval(tmp_path, monkeypatch):
    host = FakeBrowserHost(tmp_path / "chrome-profile")
    driver = FakeGuardedPageDriver(host, sites())
    runtime = RecordingRuntime(scripted(fill_form_script()))
    real_build = api_main.build_container
    ports: list[int | None] = []

    def build(cfg, **kw):
        ports.append(kw.get("port"))
        return real_build(cfg, browser=BrowserPair(host, driver), agent=runtime, **kw)

    monkeypatch.setattr(api_main, "build_container", build)
    app = create_app(settings(tmp_path), port=PORT)
    with TestClient(app, base_url=LOCAL) as client:
        c = app.state.container
        assert c.agent is runtime and c.browser is host and c.pages is driver
        assert client.get("/health").json()["server_origin"] == f"http://127.0.0.1:{PORT}"

        async def scenario() -> str:
            record = await c.applications.create(FORM)
            await c.applications.transition(record.application_id, S.QUEUED, run_id=None)
            await c.runner.enqueue(JobKind.FILL, application_id=record.application_id)

            async def settled() -> bool:
                state = await c.applications.current_state(record.application_id)
                return state is S.AWAITING_APPROVAL

            await _until(settled)
            return record.application_id

        application_id = client.portal.call(scenario)

        async def last_run():
            async with c.uow() as uow:
                history = await uow.applications.history(application_id)
                run_id = next(h.run_id for h in reversed(history) if h.run_id)
                return await uow.runs.get(run_id)

        run = client.portal.call(last_run)
        review = client.portal.call(RunArtifacts(c.store).load_review, run.run_id)
    assert ports == [PORT]
    assert run is not None and run.status is RunStatus.DONE
    assert runtime.run_ids == [run.run_id]  # CLI 작업 디렉터리가 runs/<run_id>/ 가 된다
    assert review is not None and "홍길동" in [e.value for e in review.fill_log.entries]
    assert [s for s in driver.sent if s == ("POST", FINAL)] == []  # 제출 0건
