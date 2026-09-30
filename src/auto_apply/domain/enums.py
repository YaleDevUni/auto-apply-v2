"""도메인 열거형. 외부 의존 없음 (§A2)."""

from enum import StrEnum


class ApplicationState(StrEnum):
    """지원 건 상태 (§A3). 전이 규칙은 `domain/application_state.py`.

    DB 의 state 는 이 값의 projection.
    """

    DRAFT = "draft"
    QUEUED = "queued"
    FILLING = "filling"
    NEEDS_INPUT = "needs_input"
    NEEDS_LOGIN = "needs_login"
    AWAITING_APPROVAL = "awaiting_approval"
    REVISING = "revising"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    SUBMIT_MISMATCH = "submit_mismatch"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    # 하네스 L5 가 승인 없는 제출 가능성을 감지했다(§A4). 사람이 확인해 닫는다 — 자동 재시도 없음.
    INCIDENT = "incident"


class SubmitMode(StrEnum):
    """지원 건 생성 시점의 제출 모드 스냅샷. 기본은 DRY_RUN (00-product 절대 규칙 2)."""

    DRY_RUN = "dry_run"
    LIVE = "live"


class RunKind(StrEnum):
    """에이전트 세션 1회의 종류 (§A3 runs)."""

    FILL = "fill"
    REVISE = "revise"
    SUBMIT = "submit"


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    # 프로세스가 죽거나 멈춰 끝을 못 본 run — 기동 시 크래시 복구가 RUNNING 을 이걸로
    # 닫는다(§A3, §A9).
    INTERRUPTED = "interrupted"


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
