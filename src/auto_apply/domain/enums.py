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


# ── 프로필 · 지식베이스 (§A7) ────────────────────────────────────────────────
# 값은 언어 중립 식별자다(D14) — 화면 표시 라벨은 웹 i18n 키가 맡는다.


class ExperienceKind(StrEnum):
    COMPANY = "company"
    PROJECT = "project"
    ACTIVITY = "activity"
    EDUCATION = "education"


class MilitaryStatus(StrEnum):
    COMPLETED = "completed"  # 군필
    SERVING = "serving"  # 복무 중
    NOT_COMPLETED = "not_completed"  # 미필
    EXEMPT = "exempt"  # 면제
    NOT_APPLICABLE = "not_applicable"  # 해당 없음


class DocumentKind(StrEnum):
    UPLOADED = "uploaded"  # 사용자가 올린 고정 파일 (D11)
    GENERATED = "generated"  # 공고맞춤 생성 PDF (M5)
