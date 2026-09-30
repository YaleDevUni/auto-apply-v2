"""도메인 에러와 실패 분류 = 재시도 정책 (§A9).

JobRunner 는 핸들러가 던진 예외를 `classify_failure()` 로 가른다. 재시도는 인프라성 실패
(`FailureKind.TRANSIENT`)만, 나머지는 재시도 없이 지원 건 상태 전이로 끝난다.
"""

from dataclasses import dataclass
from enum import StrEnum


class AutoApplyError(Exception):
    """모든 도메인 에러의 뿌리."""


class TerminalError(AutoApplyError):
    """재시도해도 결과가 같은 에러. 서브클래스가 따로 분류되지 않으면 FATAL 이다."""


class CaptchaEncountered(TerminalError):
    """우회하지 않는다. 즉시 사람에게 넘긴다 (§9.5)."""


class AuthRequired(TerminalError):
    """사이트 로그인 세션 만료. 사람이 전용 크롬 창에서 다시 로그인해야 한다."""


class PolicyViolation(TerminalError):
    """rate limit / allowlist / submit 경로 정책 위반 (§3)."""


class SubmitIncident(TerminalError):
    """하네스 L5 가 승인 없는 제출 흔적을 봤다 (§A4).

    사람이 사이트에서 확인해 닫는다 — 자동 재시도 없음.
    """


class InvalidTransition(TerminalError):
    """상태기계(§A3) 표에 없는 전이. 동시에 다른 쪽이 먼저 상태를 바꾼 경우도 이것이다.

    같은 요청을 다시 해도 표는 그대로라 재시도하지 않는다.
    """

    def __init__(self, current: str, to: str, detail: str = "") -> None:
        super().__init__(f"{current} -> {to} 전이 불가" + (f" ({detail})" if detail else ""))
        self.current = current
        self.to = to


class LLMSchemaViolation(AutoApplyError):
    """LLM 출력이 Pydantic 스키마를 위반. activity 내부에서 2회까지 재프롬프트."""


class LLMExecutionError(AutoApplyError):
    """LLM 호출 자체가 실패(프로세스 비정상 종료·타임아웃·응답 파싱 실패·분류 안 된 에러).

    스키마 위반과 다르다 — 재프롬프트로 고칠 문제가 아니라 대부분 일시적(타임아웃 등)이라
    재시도(TRANSIENT)로 회복될 수 있다. 재시도로 저절로 안 풀리는 두 실패 모드(로그인 풀림·
    사용량 한도)는 아래 서브클래스로 갈라 FATAL 로 사람에게 넘긴다.
    """


class LLMAuthRequired(LLMExecutionError):
    """`claude` CLI 로그인이 풀림 (`claude login` 필요).

    브라우저 storage_state 만료용 `AuthRequired`와 이름이 겹치지 않게 접두어를 다르게 뒀다.
    재시도로 안 풀리는 실패라 FATAL 이다(`ClaudeCodeCliLLM._run` 이 CLI 응답
    시그니처로 분류해서 던진다).
    """


class LLMQuotaExceeded(LLMExecutionError):
    """구독 사용량 한도(5시간/주간) 또는 `--max-budget-usd` 초과.

    리셋을 기다리거나 예산 설정을 사람이 조정해야 풀린다 — 재시도로 안 풀리는 실패라
    FATAL 이다.
    """


class BlobNotFound(AutoApplyError):
    """BlobStore 계약: 없는 키를 get 하면 이 에러 (§11.5 예외 계약)."""


class ProfileNotFound(AutoApplyError):
    """ProfileSource 계약: 없는 user_id 를 get 하면 이 에러.

    인적사항을 아직 저장하지 않았다는 뜻이라 재시도해도 결과가 같다.
    """


class UniqueIdentifierRejected(AutoApplyError, ValueError):
    """고유식별정보(주민등록번호 등)는 저장하지 않는다 (00-product 절대 규칙 5).

    ValueError 이기도 한 이유: DTO 검증기 안에서 던지면 pydantic 이 ValidationError 로 감싸
    API 가 일반 입력 검증 실패와 같은 경로로 거절할 수 있다. 메시지에 값 자체는 넣지 않는다.
    """


class AnswerKeyConflict(AutoApplyError):
    """AnswerRepository 계약: 같은 사용자의 다른 답변이 이미 같은 질문 키를 쓰고 있다."""


class NotFound(AutoApplyError):
    """요청한 항목이 없거나 이 사용자의 것이 아니다 — 둘을 구분해 알려주지 않는다 (API 404)."""


class InvalidInput(AutoApplyError, ValueError):
    """DTO 스키마는 통과했지만 서비스 규칙(유일성·참조·빈 값·multipart 모양)에 어긋난다.

    API 는 422 로 돌려준다.
    """


class UploadRejected(AutoApplyError):
    """업로드 파일을 받지 않는다. `reason` 은 `unsupported_type` | `too_large` | `empty`."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class TextExtractionFailed(AutoApplyError):
    """DocumentTextExtractor 계약: 문서에서 글자를 뽑지 못했다 (§A7 온보딩 추출).

    지원하지 않는 형식·망가진 파일·암호·텍스트 없는 스캔본·상한 초과를 전부 이 하나로 알린다 —
    어느 쪽이든 사용자가 다른 파일을 올려야 풀린다. 메시지에 문서 내용을 싣지 않는다.
    """


class ChromeNotFound(TerminalError):
    """BrowserHost 계약: 이 PC 에서 Chrome 을 찾지 못했다 (D6). 사람이 설치해야 풀린다."""


class BrowserProfileInUse(TerminalError):
    """BrowserHost 계약: 앱 전용 브라우저 프로필을 다른 호스트가 쓰고 있다.

    같은 데이터 디렉터리로 auto-apply 를 두 번 띄운 경우다 — 한 프로필을 두 브라우저가 쓰면
    쿠키·세션 파일이 깨진다. 먼저 뜬 쪽을 끄면 풀린다.
    """


class BrowserLaunchFailed(AutoApplyError):
    """BrowserHost 계약: 브라우저를 찾았지만 띄우지 못했다(크래시·시간 초과 등).

    인프라성 실패라 재시도 대상이다 (§A9).
    """


class PageFailure(StrEnum):
    STALE_REF = (
        "stale_ref"  # 최신 snapshot 의 ref 가 아니거나 요소가 사라졌다 — snapshot 을 다시 떠야 한다
    )
    UNSUPPORTED_ELEMENT = "unsupported_element"  # 이 동작을 할 수 없는 요소 (예: 버튼에 fill)
    SECRET_FIELD = "secret_field"  # 비밀번호·인증 코드 칸 — 앱은 입력하지 않는다 (절대 규칙 3)
    OPTION_NOT_FOUND = "option_not_found"
    NAVIGATION_FAILED = "navigation_failed"
    TIMEOUT = "timeout"  # 요소가 조작 가능한 상태가 되지 않았다 (가려짐·비활성 등)


class PageActionFailed(AutoApplyError):
    """PageDriver 계약: 요청한 페이지 동작을 하지 않았다(§A5). 사이트에는 아무 변화도 없다.

    run 을 끝낼 일이 아니라 에이전트가 다른 방법을 고를 일이라 도구 결과로 돌려준다.
    메시지에 입력값을 싣지 않는다.
    """

    def __init__(self, reason: PageFailure, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class SubmitGuardUnavailable(AutoApplyError):
    """제출 차단 하네스(§A4 L3·L4)를 브라우저에 설치하지 못했다 — 그 동작은 하지 않았다.

    하네스 없이 페이지를 건드리지 않는다(닫힌 쪽으로 실패). 자동 재시도하지 않는다 — 하네스가
    못 서는 브라우저에서 같은 동작을 되풀이하지 않고 사람에게 넘긴다(FATAL).
    """


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
