from typing import Protocol

from auto_apply.contracts.web_agent import (
    WebAgentFillResult,
    WebAgentSessionRef,
    WebAgentSubmitResult,
    WebAgentTask,
)


class WebAgentExecutor(Protocol):
    """범용 브라우저 에이전트(Aside)로 ATS/자체구축 폼을 실행한다 (ARCHITECTURE.md §2.4b).

    `RecipeExecutor`와 다르게 사전 검증된 액션 리스트가 아니라 자연어 task + 데이터를 건넨다 —
    그래서 안전장치를 프로토콜 레벨에서 강제한다. `fill()`과 `submit()`을 **별도 메서드로
    분리**하는 게 핵심이다: 한 메서드로 합치면 구현이 실수로/편의상 한 호출에 채움+제출을
    다 해버릴 길이 타입 레벨에서 열려버린다. `submit()`은 `fill()`이 돌려준 `session`으로만
    이어받을 수 있다 — 사람 승인 없이 자체적으로 제출까지 가는 경로가 프로토콜에 존재하지 않는다.

    계약:
      - `fill()`은 절대 제출까지 가지 않는다. 채우고 스크린샷을 찍은 상태에서 멈춘 결과만
        돌려준다.
      - 로그인이 필요한데 자격증명이 없거나 로그인이 거부되면 `WebAgentLoginFailed`.
      - 예상 못한 폼 구조 등으로 과제 자체가 실패하면 `WebAgentTaskFailed`.
      - CAPTCHA 를 만나면 `CaptchaEncountered`(RecipeExecutor 와 동일 — 우회하지 않는다).
      - 분류되지 않은 subprocess/타임아웃 실패는 `WebAgentExecutionError`(재시도 가능).
    """

    async def fill(self, task: WebAgentTask) -> WebAgentFillResult: ...

    async def submit(self, session: WebAgentSessionRef) -> WebAgentSubmitResult: ...
