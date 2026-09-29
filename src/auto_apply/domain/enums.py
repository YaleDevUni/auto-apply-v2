"""도메인 열거형. 외부 의존 없음 (§A2).

v3 상태기계(§A3)는 M4 에서 `domain/application_state.py` 로 다시 짠다 — 아래는 그 전까지
repository projection 이 쓰는 v2 상태값이다.
"""

from enum import StrEnum


class ApplicationState(StrEnum):
    """DB 의 status 는 이 값의 projection."""

    COLLECTING = "collecting"
    EVALUATING = "evaluating"
    GENERATING_RESUME = "generating_resume"
    REVIEWING = "reviewing"
    RENDERING_PDF = "rendering_pdf"
    AWAITING_APPROVAL = "awaiting_approval"
    SCHEDULED = "scheduled"
    EXECUTING = "executing"
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
