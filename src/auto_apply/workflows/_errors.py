"""ActivityError → (실패 타입, 사람이 읽을 사유) 추출 (ARCHITECTURE.md §5).

Temporal 은 activity 가 던진 예외를 ApplicationError 로 감싸고 원래 클래스는 `.type` 문자열로만
남긴다(CLAUDE.md "Temporal 관련 주의") — 그래서 isinstance 가 아니라 이 함수로 타입을 비교해야
한다. `application.py`/`_execution.py` 둘 다 같은 파싱이 필요해서 여기로 뺐다.
"""

from temporalio.exceptions import ActivityError, ApplicationError


def activity_failure(e: ActivityError) -> tuple[str, str]:
    """(failure_type, reason). failure_type 은 원래 예외 클래스명, 모르면 "unknown"."""
    cause = e.cause
    failure_type = cause.type if isinstance(cause, ApplicationError) else None
    reason = f"{failure_type or 'unknown'}: {cause}"
    return failure_type or "unknown", reason
