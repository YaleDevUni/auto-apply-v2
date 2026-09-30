"""fill 핸들러를 JobRunner 에 물린 경로 (§A9) — 재시도·비재시도·취소 경합·정지."""

import asyncio

import pytest

from auto_apply.adapters.agent.scripted import ScriptedAgentRuntime
from auto_apply.contracts.agent import AgentLimits, AgentOutcome
from auto_apply.contracts.jobs import JobKind, JobStatus
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.enums import RunStatus
from auto_apply.domain.errors import LLMExecutionError
from tests.runner.fill_kit import FillRig, fill_form_script
from tests.runner.kit import wait_until


@pytest.fixture
async def rig(tmp_path):
    rig = FillRig(tmp_path)
    yield rig
    await rig.host.close()


async def _runs(rig: FillRig, application_id: str) -> list:
    ids = dict.fromkeys(h.run_id for h in await rig.history(application_id) if h.run_id)
    async with rig.uow() as u:
        return [await u.runs.get(i) for i in ids]


async def _enqueue_and_wait(rig, handler, *, until):
    app = await rig.app_in(S.QUEUED)
    runner = rig.runner({JobKind.FILL: handler})
    await runner.start()
    try:
        job = await runner.enqueue(JobKind.FILL, application_id=app)
        await wait_until(lambda: until(app, job))
    finally:
        await runner.stop()
    return app, job


async def test_fill_job_reaches_awaiting_approval(rig):
    handler = rig.handler(ScriptedAgentRuntime(fill_form_script()))

    async def done(app, job):
        return (await rig.job(job.job_id)).status is JobStatus.DONE

    app, _ = await _enqueue_and_wait(rig, handler, until=done)
    assert await rig.state(app) is S.AWAITING_APPROVAL
    assert rig.final_submits() == []


async def test_runtime_failure_closes_run_and_retries(rig):
    class FlakyOnce:
        def __init__(self) -> None:
            self.ok = ScriptedAgentRuntime(fill_form_script())
            self.calls = 0

        async def run(self, system_prompt, tools, call_tool, *, limits) -> AgentOutcome:
            self.calls += 1
            if self.calls == 1:
                raise LLMExecutionError("claude 프로세스가 비정상 종료")
            return await self.ok.run(system_prompt, tools, call_tool, limits=limits)

    handler = rig.handler(FlakyOnce())

    async def approved(app, job):
        return await rig.state(app) is S.AWAITING_APPROVAL

    app, _ = await _enqueue_and_wait(rig, handler, until=approved)
    first, second = await _runs(rig, app)
    assert first.status is RunStatus.FAILED and "LLMExecutionError" in first.error
    assert second.status is RunStatus.DONE
    states = [h.state for h in await rig.history(app)]
    assert states[1:] == [S.QUEUED, S.FILLING, S.QUEUED, S.FILLING, S.AWAITING_APPROVAL]


async def test_guard_unavailable_is_not_retried(rig):
    rig.driver.fail_arm = True
    handler = rig.handler(ScriptedAgentRuntime(fill_form_script()))

    async def done(app, job):
        return (await rig.job(job.job_id)).status is JobStatus.DONE

    app, _ = await _enqueue_and_wait(rig, handler, until=done)
    assert await rig.state(app) is S.FAILED
    assert await rig.jobs(JobStatus.QUEUED) == []
    assert len(await _runs(rig, app)) == 1


async def test_cancel_during_run_wins_and_run_is_closed(rig):
    class CancelledMidway:
        """채우는 도중 사람이 취소했다 — 끝 전이는 거부되고 run 은 그래도 닫힌다."""

        async def run(self, system_prompt, tools, call_tool, *, limits) -> AgentOutcome:
            inner = ScriptedAgentRuntime(fill_form_script())
            app = rig.toolboxes[0]._application_id
            assert app is not None
            await rig.apps.transition(app, S.CANCELLED, run_id=None)
            return await inner.run(system_prompt, tools, call_tool, limits=limits)

    handler = rig.handler(CancelledMidway())

    async def failed(app, job):
        return (await rig.job(job.job_id)).status is JobStatus.FAILED

    app, _ = await _enqueue_and_wait(rig, handler, until=failed)
    assert await rig.state(app) is S.CANCELLED
    (run,) = await _runs(rig, app)
    assert run.status is RunStatus.FAILED and run.finished_at is not None


async def test_stop_mid_run_interrupts_and_keeps_guard_armed(rig):
    started = asyncio.Event()

    class Hangs:
        async def run(self, system_prompt, tools, call_tool, *, limits) -> AgentOutcome:
            await call_tool("navigate", {"url": "http://jobs.test/apply"})
            started.set()
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    handler = rig.handler(Hangs(), limits=AgentLimits(max_seconds=3600))
    app = await rig.app_in(S.QUEUED)
    runner = rig.runner({JobKind.FILL: handler})
    await runner.start()
    await runner.enqueue(JobKind.FILL, application_id=app)
    await asyncio.wait_for(started.wait(), timeout=5)
    await runner.stop()
    (run,) = await _runs(rig, app)
    assert run.status is RunStatus.INTERRUPTED
    assert await rig.state(app) is S.QUEUED  # 크래시 복구 간선 — 다음 기동에 다시 돈다
    assert rig.driver.armed is True  # 정지 중에는 가드를 내리지 않는다(닫힌 쪽)
