"""제출하지 않는 WebAgentExecutor 테스트 대역 (`adapters/executor/replay.py`와 같은 성격).

실제 Aside subprocess/브라우저 없이 fill/submit 계약(§2.4b)을 재현한다 — `AsideCliExecutor`와
같은 contract test 를 돌리기 위해 존재한다.
"""

from datetime import UTC

from auto_apply.contracts.web_agent import (
    WebAgentFillResult,
    WebAgentSessionRef,
    WebAgentSubmitResult,
    WebAgentTask,
)
from auto_apply.domain.enums import AttemptOutcome
from auto_apply.domain.errors import (
    CaptchaEncountered,
    WebAgentLoginFailed,
    WebAgentTaskFailed,
)
from auto_apply.ports.clock import Clock


class ReplayWebAgentExecutor:
    def __init__(
        self,
        clock: Clock,
        *,
        login_fail_keys: frozenset[str] = frozenset(),
        captcha_urls: frozenset[str] = frozenset(),
        task_fail_urls: frozenset[str] = frozenset(),
    ) -> None:
        self._clock = clock
        # 셋 다 테스트에서 실제 subprocess 없이 executor 계약(§11.2)을 재현하는 주입점.
        self._login_fail_keys = login_fail_keys
        self._captcha_urls = captcha_urls
        self._task_fail_urls = task_fail_urls
        self._sessions: dict[str, WebAgentTask] = {}
        self._counter = 0

    async def fill(self, task: WebAgentTask) -> WebAgentFillResult:
        if task.apply_url in self._captcha_urls:
            raise CaptchaEncountered(f"{task.apply_url} 에서 CAPTCHA 감지(replay)")
        if task.credential_key and task.credential_key in self._login_fail_keys:
            raise WebAgentLoginFailed(f"'{task.credential_key}' 계정 로그인 실패(replay)")
        if task.apply_url in self._task_fail_urls:
            raise WebAgentTaskFailed(
                f"{task.apply_url} 채우기 실패(replay)",
                screenshot_key=f"application-artifacts/{task.application_id}/fill-failed.png",
            )

        self._counter += 1
        session_id = f"replay-session-{self._counter}"
        self._sessions[session_id] = task
        return WebAgentFillResult(
            session=WebAgentSessionRef(session_id=session_id),
            screenshot_key=f"application-artifacts/{task.application_id}/web-agent-fill.png",
            summary=f"{task.apply_url} 채움 (replay)",
        )

    async def submit(self, session: WebAgentSessionRef) -> WebAgentSubmitResult:
        task = self._sessions.pop(session.session_id, None)
        if task is None:
            raise WebAgentTaskFailed(
                f"알 수 없는 세션: {session.session_id}(replay)", screenshot_key=""
            )
        return WebAgentSubmitResult(
            outcome=AttemptOutcome.SUCCEEDED,
            submitted_at=self._clock.now().astimezone(UTC),
            detail=f"replay submit: {task.apply_url}",
        )
