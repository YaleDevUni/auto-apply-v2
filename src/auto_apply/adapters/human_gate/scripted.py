"""HumanGate 테스트 대역 — 사람 대신 미리 적은 대로 답한다 (§A5).

스크립트의 한 걸음이 `wait()` 한 번이다: 답(`HumanReply`)이면 바로 답하고, 함수면 사람이 할 일
(쿠키 심기·로그인 폼 제출 등)을 한 뒤 그 반환값으로 답한다(None 이면 답하지 않는다). 걸음이
`None` 이거나 스크립트가 바닥나면 아무도 답하지 않는다 — 밖에서 `answer()` 하거나 타임아웃이다.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Iterable

from auto_apply.adapters.human_gate.memory import InMemoryHumanGate
from auto_apply.contracts.human_gate import HumanReply, HumanTask

HumanStep = HumanReply | Callable[[HumanTask], Awaitable[HumanReply | None]] | None


class ScriptedHumanGate:
    def __init__(self, script: Iterable[HumanStep] = ()) -> None:
        self._gate = InMemoryHumanGate()
        self._script: deque[HumanStep] = deque(script)
        self.asked: list[HumanTask] = []  # 기다린 일 — 테스트가 무엇을 부탁받았는지 본다

    async def wait(self, task: HumanTask, *, timeout_s: float) -> HumanReply:
        step = self._script.popleft() if self._script else None
        self.asked.append(task)
        if step is None:
            return await self._gate.wait(task, timeout_s=timeout_s)
        # 행위자는 이 코루틴이 처음 양보할 때 돈다 — 그때 게이트는 이미 일을 등록했다.
        actor = asyncio.create_task(self._act(task, step))
        try:
            return await self._gate.wait(task, timeout_s=timeout_s)
        finally:
            if not actor.done():  # 타임아웃·취소 — 사람 흉내도 멈춘다
                actor.cancel()

    async def _act(self, task: HumanTask, step: HumanStep) -> None:
        reply = step if isinstance(step, HumanReply) or step is None else await step(task)
        if reply is not None:
            await self._gate.answer(task.id, reply)

    def pending(self) -> tuple[HumanTask, ...]:
        return self._gate.pending()

    async def answer(self, task_id: str, reply: HumanReply) -> bool:
        return await self._gate.answer(task_id, reply)
