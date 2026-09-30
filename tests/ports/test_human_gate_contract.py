"""HumanGate contract test (ports/human_gate.py) — 인메모리와 스크립트 대역에 같은 기대를 건다.

스크립트 대역은 빈 스크립트로 만든다(아무도 자동으로 답하지 않는다) — 계약은 밖에서 answer() 하는
UI 경로다. 스크립트가 답하는 동작은 아래 따로 본다.
"""

import asyncio
from collections.abc import Callable

import pytest

from auto_apply.adapters.human_gate.memory import InMemoryHumanGate
from auto_apply.adapters.human_gate.scripted import ScriptedHumanGate
from auto_apply.contracts.human_gate import HumanOutcome, HumanReply, HumanTask, HumanTaskKind
from auto_apply.domain.errors import InvalidInput
from auto_apply.ports.human_gate import HumanGate

DONE = HumanReply(outcome=HumanOutcome.DONE, note="로그인했어요")
MAKERS: dict[str, Callable[[], HumanGate]] = {
    "memory": InMemoryHumanGate,
    "scripted": ScriptedHumanGate,
}


def _task(task_id: str = "t1", kind: HumanTaskKind = HumanTaskKind.LOGIN) -> HumanTask:
    return HumanTask(id=task_id, kind=kind, application_id="app_1", run_id="run_1", site="짐")


@pytest.fixture(params=sorted(MAKERS))
def gate(request: pytest.FixtureRequest) -> HumanGate:
    return MAKERS[request.param]()


async def _until_pending(gate: HumanGate, n: int = 1) -> None:
    for _ in range(1000):
        if len(gate.pending()) >= n:
            return
        await asyncio.sleep(0)
    raise AssertionError("기다리는 일이 없다")


async def test_answer_resumes_the_waiter(gate):
    waiter = asyncio.create_task(gate.wait(_task(), timeout_s=5))
    await _until_pending(gate)
    assert [t.id for t in gate.pending()] == ["t1"] and gate.pending()[0].site == "짐"
    assert await gate.answer("t1", DONE)
    assert await waiter == DONE
    assert gate.pending() == ()


async def test_timeout_is_a_reply_not_an_exception(gate):
    reply = await gate.wait(_task(), timeout_s=0.01)
    assert reply.outcome is HumanOutcome.TIMED_OUT
    assert gate.pending() == ()
    assert not await gate.answer("t1", DONE)  # 늦게 온 답은 받지 않는다


async def test_only_the_first_answer_counts(gate):
    waiter = asyncio.create_task(gate.wait(_task(), timeout_s=5))
    await _until_pending(gate)
    declined = HumanReply(outcome=HumanOutcome.DECLINED)
    assert await gate.answer("t1", declined)
    assert not await gate.answer("t1", DONE)
    assert await waiter == declined


async def test_unknown_task_and_gate_only_outcome_are_refused(gate):
    assert not await gate.answer("nope", DONE)
    with pytest.raises(InvalidInput):
        await gate.answer("nope", HumanReply(outcome=HumanOutcome.TIMED_OUT))


async def test_same_task_cannot_wait_twice(gate):
    first = asyncio.create_task(gate.wait(_task(), timeout_s=5))
    await _until_pending(gate)
    with pytest.raises(InvalidInput):
        await gate.wait(_task(), timeout_s=5)
    assert await gate.answer("t1", DONE) and await first == DONE


async def test_cancelled_wait_leaves_nothing_pending(gate):
    waiter = asyncio.create_task(gate.wait(_task(), timeout_s=5))
    await _until_pending(gate)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert gate.pending() == ()
    assert not await gate.answer("t1", DONE)


async def test_two_tasks_are_independent(gate):
    a = asyncio.create_task(gate.wait(_task("a"), timeout_s=5))
    b = asyncio.create_task(gate.wait(_task("b", HumanTaskKind.INPUT), timeout_s=5))
    await _until_pending(gate, 2)
    assert await gate.answer("b", DONE)
    assert await b == DONE and not a.done()
    assert [t.id for t in gate.pending()] == ["a"]
    assert await gate.answer("a", DONE) and await a == DONE


# ---------------------------------------------------------------- 스크립트 대역만의 동작
async def test_scripted_gate_acts_then_answers_in_order():
    done: list[str] = []

    async def human(task: HumanTask) -> HumanReply:
        done.append(task.id)  # 사람이 할 일(쿠키 심기 등)
        return DONE

    gate = ScriptedHumanGate([human, HumanReply(outcome=HumanOutcome.DECLINED), None])
    assert await gate.wait(_task("a"), timeout_s=5) == DONE and done == ["a"]
    assert (await gate.wait(_task("b"), timeout_s=5)).outcome is HumanOutcome.DECLINED
    assert (await gate.wait(_task("c"), timeout_s=0.01)).outcome is HumanOutcome.TIMED_OUT
    assert [t.id for t in gate.asked] == ["a", "b", "c"]


async def test_scripted_actor_is_stopped_on_timeout():
    started = asyncio.Event()

    async def slow(_: HumanTask) -> HumanReply:
        started.set()
        await asyncio.sleep(60)
        return DONE

    gate = ScriptedHumanGate([slow])
    assert (await gate.wait(_task(), timeout_s=0.05)).outcome is HumanOutcome.TIMED_OUT
    assert started.is_set() and gate.pending() == ()
