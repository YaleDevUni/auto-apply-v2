"""HumanGate — 한 프로세스 안의 asyncio Future (§A5). 앱은 단일 프로세스라 이것이 실제 구현이다.

run 과 UI 요청 처리(M3/M4)가 같은 이벤트 루프에 있어야 한다.
"""

import asyncio
import contextlib

import structlog

from auto_apply.contracts.human_gate import HumanOutcome, HumanReply, HumanTask
from auto_apply.domain.errors import InvalidInput

log = structlog.get_logger(__name__)


class InMemoryHumanGate:
    def __init__(self) -> None:
        self._waiting: dict[str, tuple[HumanTask, asyncio.Future[HumanReply]]] = {}

    async def wait(self, task: HumanTask, *, timeout_s: float) -> HumanReply:
        if task.id in self._waiting:
            raise InvalidInput("같은 일이 이미 사람을 기다리고 있다")
        future: asyncio.Future[HumanReply] = asyncio.get_running_loop().create_future()
        self._waiting[task.id] = (task, future)
        bound = log.bind(application_id=task.application_id, run_id=task.run_id)
        bound.info("human_gate.waiting", task_id=task.id, kind=task.kind.value)
        try:
            reply = await asyncio.wait_for(asyncio.shield(future), timeout=max(timeout_s, 0.0))
        except TimeoutError:
            reply = HumanReply(outcome=HumanOutcome.TIMED_OUT)
        finally:
            # 답·타임아웃·호출 취소 어느 쪽이든 목록에서 뺀다 — 늦게 온 답은 answer() 가 False
            self._waiting.pop(task.id, None)
            if not future.done():
                future.cancel()
        bound.info("human_gate.done", task_id=task.id, outcome=reply.outcome.value)
        return reply

    def pending(self) -> tuple[HumanTask, ...]:
        return tuple(task for task, _ in self._waiting.values())

    async def answer(self, task_id: str, reply: HumanReply) -> bool:
        if reply.outcome is HumanOutcome.TIMED_OUT:
            raise InvalidInput("timed_out 은 게이트만 정한다")
        entry = self._waiting.get(task_id)
        if entry is None or entry[1].done():
            return False
        with contextlib.suppress(asyncio.InvalidStateError):
            entry[1].set_result(reply)
            return True
        return False
