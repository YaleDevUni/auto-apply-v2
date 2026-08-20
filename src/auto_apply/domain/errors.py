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


class LLMExecutionError(AutoApplyError):
    """LLM 호출 자체가 실패(프로세스 비정상 종료·타임아웃·응답 파싱 실패·분류 안 된 에러).

    스키마 위반과 다르다 — 재프롬프트로 고칠 문제가 아니라 대부분 일시적(타임아웃 등)이라
    재시도로 회복될 수 있어 NON_RETRYABLE 에 넣지 않는다. 재시도로 저절로 안 풀리는 두
    실패 모드(로그인 풀림·사용량 한도)는 아래 서브클래스로 갈라서 사람에게 알린다.
    """


class LLMAuthRequired(LLMExecutionError):
    """`claude` CLI 로그인이 풀림 (`claude login` 필요).

    브라우저 storage_state 만료용 `AuthRequired`와 이름이 겹치지 않게 접두어를 다르게 뒀다.
    재시도로 안 풀리는 실패라 NON_RETRYABLE 이고, workflow 가 이 타입을 보면 사람에게
    텔레그램으로 알린다(`ClaudeCodeCliLLM._run` 이 CLI 응답 시그니처로 분류해서 던진다).
    """


class LLMQuotaExceeded(LLMExecutionError):
    """구독 사용량 한도(5시간/주간) 또는 `--max-budget-usd` 초과.

    리셋을 기다리거나 예산 설정을 사람이 조정해야 풀린다 — 재시도로 안 풀리는 실패라
    NON_RETRYABLE 이고, workflow 가 이 타입을 보면 사람에게 텔레그램으로 알린다.
    """


class BlobNotFound(AutoApplyError):
    """BlobStore 계약: 없는 키를 get 하면 이 에러 (§11.5 예외 계약)."""


class ProfileNotFound(AutoApplyError):
    """ProfileSource 계약: 없는 user_id 를 get 하면 이 에러.

    config/profile.yaml 미기재 = 설정 오류라 재시도해도 결과가 같다.
    """


class AlreadySubmitted(AutoApplyError):
    """멱등 충돌. verify 로 확인한 뒤 성공으로 간주한다."""


class GuidePatchNotFound(TerminalError):
    """domain/guide_patch.py: LLM 이 낸 `old` 가 현재 가이드 본문에 한 번도 없다.

    가이드가 그 사이 바뀌었거나(§ resume-revise-feedback-design, 동시 REVISE) LLM 이
    본문을 살짝 바꿔 인용했다는 뜻 — 재시도해도 같은 `old`로는 또 안 맞으니 재시도하지 않는다.
    """


class GuidePatchAmbiguous(TerminalError):
    """domain/guide_patch.py: `old` 가 가이드 본문에 2번 이상 나타나 어디를 바꿀지 모호하다."""


class WebAgentLoginFailed(TerminalError):
    """WebAgentExecutor: credential 큐에 키가 없거나, 그 계정으로 로그인이 거부됐다.

    사람이 config/credentials.json 을 고쳐야 풀린다 — 재시도해도 같은 자격증명으로는
    똑같이 실패한다.
    """


class WebAgentTaskFailed(TerminalError):
    """WebAgentExecutor: 예상 못한 폼 구조 등으로 과제 자체가 실패했다.

    `RecipeExecutionError`처럼 `screenshot_key`를 들고 있어 사람이 원인을 볼 수 있다 — 단
    `form_hash`/repair 경로는 없다. Recipe 처럼 재사용되는 대상이 없는 1회성 실행이라
    "수선해서 다음에 또 쓴다"는 개념 자체가 없다.
    """

    def __init__(self, message: str, *, screenshot_key: str) -> None:
        super().__init__(message)
        self.screenshot_key = screenshot_key


class WebAgentExecutionError(AutoApplyError):
    """WebAgentExecutor: 분류되지 않은 subprocess 실패(타임아웃·비정상 종료·출력 파싱 실패).

    `LLMExecutionError`와 같은 이유로 NON_RETRYABLE 에 넣지 않는다 — 대부분 일시적이라
    재시도로 회복될 수 있다.
    """


NON_RETRYABLE: tuple[str, ...] = (
    RecipeExecutionError.__name__,
    CaptchaEncountered.__name__,
    AuthRequired.__name__,
    PolicyViolation.__name__,
    EligibilityRejected.__name__,
    # generator 가 이미 내부에서 2회 재프롬프트했다(§5) — activity 레벨 재시도는
    # 같은 실패를 반복할 뿐이라 여기서 non-retryable 로 끊는다.
    LLMSchemaViolation.__name__,
    ProfileNotFound.__name__,
    # 재시도로 저절로 안 풀리는 claude CLI 실패 — 사람이 개입해야 한다 (로그인/한도 리셋).
    LLMAuthRequired.__name__,
    LLMQuotaExceeded.__name__,
    GuidePatchNotFound.__name__,
    GuidePatchAmbiguous.__name__,
    WebAgentLoginFailed.__name__,
    WebAgentTaskFailed.__name__,
)
