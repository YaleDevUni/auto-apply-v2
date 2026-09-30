"""실패 분류 = 재시도 정책 (§A9).

JobRunner 는 핸들러가 던진 예외를 `classify_failure()` 로 가른다. 재시도는 인프라성 실패
(`FailureKind.TRANSIENT`)만, 나머지는 재시도 없이 지원 건 상태 전이로 끝난다.
"""

from dataclasses import dataclass
from enum import StrEnum

from auto_apply.domain.errors import (
    AuthRequired,
    BrowserLaunchFailed,
    CaptchaEncountered,
    InvalidTransition,
    LLMAuthRequired,
    LLMExecutionError,
    LLMQuotaExceeded,
    SubmitGuardUnavailable,
    SubmitIncident,
    TerminalError,
)


class FailureKind(StrEnum):
    """핸들러 실패의 갈래 (§A9). 지원 건이 갈 곳은 `application_state.failure_target`."""

    TRANSIENT = "transient"  # 인프라성(브라우저 크래시·CLI 일시 오류) — 백오프 재시도
    NEEDS_LOGIN = "needs_login"  # 사이트 로그인 필요 — 사람이 전용 창에서
    NEEDS_INPUT = "needs_input"  # 사람이 풀어야 함(CAPTCHA 등 — 우회하지 않는다)
    INCIDENT = "incident"  # 승인 없는 제출 흔적 (§A4 L5)
    CONFLICT = "conflict"  # 그 사이 다른 쪽이 상태를 바꿨다(취소 등) — 지원 건을 건드리지 않는다
    FATAL = "fatal"  # 재시도해도 같다 — FAILED, 사람이 다시 시작


# 위에서부터 첫 일치. 서브클래스가 부모보다 먼저 와야 한다(LLMAuthRequired < LLMExecutionError).
_FAILURE_TABLE: tuple[tuple[type[BaseException], FailureKind], ...] = (
    (SubmitIncident, FailureKind.INCIDENT),
    (AuthRequired, FailureKind.NEEDS_LOGIN),
    (CaptchaEncountered, FailureKind.NEEDS_INPUT),
    (InvalidTransition, FailureKind.CONFLICT),
    (TerminalError, FailureKind.FATAL),
    (LLMAuthRequired, FailureKind.FATAL),
    (LLMQuotaExceeded, FailureKind.FATAL),
    (SubmitGuardUnavailable, FailureKind.FATAL),
    (BrowserLaunchFailed, FailureKind.TRANSIENT),
    (LLMExecutionError, FailureKind.TRANSIENT),
)


def classify_failure(exc: BaseException) -> FailureKind:
    """모르는 예외는 FATAL — 인프라성이라고 확인된 것만 되풀이한다(닫힌 쪽)."""
    for kind, failure in _FAILURE_TABLE:
        if isinstance(exc, kind):
            return failure
    return FailureKind.FATAL


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """TRANSIENT 실패의 지수 백오프. `attempt` 는 1부터 — `max_attempts` 번째 실패면 그만둔다."""

    max_attempts: int = 3
    base_delay_s: float = 5.0
    factor: float = 2.0
    max_delay_s: float = 120.0

    def should_retry(self, attempt: int) -> bool:
        return attempt < self.max_attempts

    def delay_s(self, attempt: int) -> float:
        return min(self.base_delay_s * self.factor ** (attempt - 1), self.max_delay_s)
