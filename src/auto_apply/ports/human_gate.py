from typing import Protocol

from auto_apply.contracts.human_gate import HumanReply, HumanTask


class HumanGate(Protocol):
    """실행(run)이 사람의 응답을 기다리는 통로 (§A5 request_login·request_human).

    구현은 adapters/human_gate/(인메모리 · 스크립트 대역)이고
    tests/ports/test_human_gate_contract.py 가 둘에 같은 기대를 건다. UI 연결(M3/M4)은
    `pending()` 으로 보여 주고 `answer()` 로 답한다.

    계약:
    - `wait()` 는 사람이 `answer()` 할 때까지, 최대 `timeout_s` 초 기다린다. 넘기면
      `HumanReply(outcome=TIMED_OUT)` 을 돌려준다(예외가 아니다). 기다리는 동안만 `pending()` 에
      있다 — 답·타임아웃·호출 취소 어느 쪽으로 끝나도 빠진다.
    - 같은 id 의 일이 이미 기다리는 중이면 `wait()` 는 `InvalidInput`.
    - `answer()` 는 기다리는 일에 처음 준 답만 받고 True. 모르는 id·이미 끝난 일은 False.
      `TIMED_OUT` 은 게이트만 만든다 — 답으로 주면 `InvalidInput`.
    """

    async def wait(self, task: HumanTask, *, timeout_s: float) -> HumanReply: ...

    def pending(self) -> tuple[HumanTask, ...]: ...

    async def answer(self, task_id: str, reply: HumanReply) -> bool: ...
