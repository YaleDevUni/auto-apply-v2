"""JobRunner — API 와 같은 프로세스에서 도는 단일 asyncio 소비자 (§A1, §A9).

M0 에서는 소비할 `jobs` 테이블도 핸들러도 없다. 기동·정지 수명주기만 먼저 세워 두고,
fill/revise/submit/generate 핸들러와 큐 소비는 M3~M5 에서 이 루프에 붙인다.
"""

import asyncio

import structlog

log = structlog.get_logger(__name__)


class JobRunner:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="job-runner")
        log.info("job_runner.started", application_id=None, run_id=None)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None
        log.info("job_runner.stopped", application_id=None, run_id=None)

    async def _loop(self) -> None:
        await self._stop.wait()
