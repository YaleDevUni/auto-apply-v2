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
