"""에러 분류 = 재시도 정책 (ARCHITECTURE.md §5).

Temporal RetryPolicy 는 예외 '이름'으로 재시도 여부를 판단한다.
그래서 재시도 금지 에러는 반드시 NON_RETRYABLE 에 등록해야 한다.
"""


class AutoApplyError(Exception):
    """모든 도메인 에러의 뿌리."""


class TerminalError(AutoApplyError):
    """재시도해도 결과가 같은 에러. Temporal 에 non-retryable 로 전달된다."""


class RecipeExecutionError(TerminalError):
    """Recipe 가 현재 DOM 과 맞지 않음 → AutomationRepairWorkflow 로 넘긴다."""

    def __init__(self, message: str, *, snapshot_key: str, form_hash: str) -> None:
        super().__init__(message)
        self.snapshot_key = snapshot_key
        self.form_hash = form_hash


class CaptchaEncountered(TerminalError):
    """우회하지 않는다. 즉시 사람에게 넘긴다 (§9.5)."""


class AuthRequired(TerminalError):
    """storage_state 만료. 사용자에게 재로그인을 요청한다."""


class PolicyViolation(TerminalError):
    """rate limit / allowlist / submit 경로 정책 위반 (§3)."""


class EligibilityRejected(TerminalError):
    """자격 미달. 실패가 아니라 정상 종료."""


class LLMSchemaViolation(AutoApplyError):
    """LLM 출력이 Pydantic 스키마를 위반. activity 내부에서 2회까지 재프롬프트."""


class BlobNotFound(AutoApplyError):
    """BlobStore 계약: 없는 키를 get 하면 이 에러 (§11.5 예외 계약)."""


class AlreadySubmitted(AutoApplyError):
    """멱등 충돌. verify 로 확인한 뒤 성공으로 간주한다."""


NON_RETRYABLE: tuple[str, ...] = (
    RecipeExecutionError.__name__,
    CaptchaEncountered.__name__,
    AuthRequired.__name__,
    PolicyViolation.__name__,
    EligibilityRejected.__name__,
    # generator 가 이미 내부에서 2회 재프롬프트했다(§5) — activity 레벨 재시도는
    # 같은 실패를 반복할 뿐이라 여기서 non-retryable 로 끊는다.
    LLMSchemaViolation.__name__,
)
