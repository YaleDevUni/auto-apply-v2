"""도메인 열거형. 외부 의존 없음 (ARCHITECTURE.md §11.6)."""

from enum import StrEnum


class ApplicationState(StrEnum):
    """ARCHITECTURE.md §2.2 상태 기계. DB 의 status 는 이 값의 projection."""

    COLLECTING = "collecting"
    EVALUATING = "evaluating"
    GENERATING_RESUME = "generating_resume"
    REVIEWING = "reviewing"
    RENDERING_PDF = "rendering_pdf"
    AWAITING_APPROVAL = "awaiting_approval"
    SCHEDULED = "scheduled"
    EXECUTING = "executing"
    REPAIRING = "repairing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    NEEDS_HUMAN = "needs_human"


TERMINAL_STATES: frozenset[ApplicationState] = frozenset(
    {
        ApplicationState.COMPLETED,
        ApplicationState.REJECTED,
        ApplicationState.CANCELLED,
        ApplicationState.EXPIRED,
        ApplicationState.NEEDS_HUMAN,
    }
)


class RecipeStatus(StrEnum):
    """§2.4 — AI 가 만든 Recipe 는 draft 에서 시작하며 사람만 active 로 올린다."""

    DRAFT = "draft"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class ExecutionMode(StrEnum):
    """RecipeExecutor 의 교체 축 (§11.2)."""

    DRY_RUN = "dry_run"  # submit 직전까지만
    SUPERVISED = "supervised"  # submit 직전 사람 확인
    LIVE = "live"  # 전자동 (active recipe 전용)


class AttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"
    UNKNOWN = "unknown"  # submit 도중 크래시 → verify 필요 (§5)


class DecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"  # 텔레그램 3번째 갈래(수정요청) — RevisionScope 로 반영 범위를 가른다


class RevisionScope(StrEnum):
    """REVISE 피드백의 저장 위치를 가른다 (메모리 resume-revise-feedback-design 참고).

    SPECIFIC 은 이번 workflow 인스턴스의 재생성에만 쓰이고 영속 저장되지 않는다. GENERAL 은
    config/resume_guide.md 에 반영돼 앞으로 모든 이력서 생성에 영향을 준다 — 되돌리기 어려운
    레버라 사람이 diff 를 한 번 더 승인해야 반영된다.
    """

    SPECIFIC = "specific"
    GENERAL = "general"


class BlockerCode(StrEnum):
    """domain/job_applicability.py 가 매기는 코드. 고정 집합이라 enum 이다.

    (하드컷/트랙 코드는 반대로 config 파일이 정의하는 데이터라 str 로 남겨둔다 —
    사용자가 config/matching.yaml 에 새 하드컷을 추가해도 코드 변경이 필요 없어야 한다.)
    """

    SCORE_BELOW_BAR = "SCORE_BELOW_BAR"
    CLOSED = "CLOSED"
    CLOSING_TOO_SOON = "CLOSING_TOO_SOON"
    EXTERNAL_ATS = "EXTERNAL_ATS"
    IMAGE_ONLY = "IMAGE_ONLY"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    NO_RECIPE = "NO_RECIPE"
    REQUIREMENT_GAP = "REQUIREMENT_GAP"
    ESSAY_REQUIRED = "ESSAY_REQUIRED"
    ESSAY_TOO_MANY = "ESSAY_TOO_MANY"
    DOC_MISSING = "DOC_MISSING"
    NO_DETAIL = "NO_DETAIL"
