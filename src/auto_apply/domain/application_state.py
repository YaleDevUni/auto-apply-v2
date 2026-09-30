"""지원 상태기계 전이 표 (§A3). 순수 함수 — 상태를 쓰는 건 `ApplicationService.transition()` 하나다.

표에 없는 전이는 `InvalidTransition`. 크래시 복구·재시도 간선(→QUEUED)도 여기 있어야 한다 —
JobRunner(§A9)는 이 표를 거쳐서만 지원 건을 되돌린다.
"""

from types import MappingProxyType

from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.errors import FailureKind, InvalidTransition

INITIAL_STATE = S.DRAFT

# 사람이 닫았거나 사이트에 제출된 상태. 더 갈 곳이 없다.
FINAL_STATES: frozenset[S] = frozenset({S.SUBMITTED, S.REJECTED, S.CANCELLED})

_EDGES: dict[S, frozenset[S]] = {
    S.DRAFT: frozenset({S.QUEUED}),
    # 핸들러가 시작도 못 하고 실패(가드 설치 불가 등)하면 QUEUED→FAILED.
    S.QUEUED: frozenset({S.FILLING, S.FAILED}),
    # →QUEUED: 인프라 실패 재시도·크래시 복구로 fill run 을 다시 줄 세운다(§A9).
    S.FILLING: frozenset(
        {S.AWAITING_APPROVAL, S.NEEDS_INPUT, S.NEEDS_LOGIN, S.FAILED, S.INCIDENT, S.QUEUED}
    ),
    # →FILLING: 같은 run 안에서 사람이 답·로그인을 마쳐 이어감. →QUEUED: 대기가 끝난 뒤 재진입 run.
    S.NEEDS_INPUT: frozenset({S.FILLING, S.QUEUED}),
    S.NEEDS_LOGIN: frozenset({S.FILLING, S.QUEUED}),
    S.AWAITING_APPROVAL: frozenset({S.SUBMITTING, S.REVISING, S.REJECTED}),
    # →AWAITING_APPROVAL 은 revise 완료이자 revise 크래시 복구(직전 검토 상태로 — 제출 전
    # L6 대조가 다시 본다).
    S.REVISING: frozenset({S.AWAITING_APPROVAL, S.FAILED, S.INCIDENT}),
    # →FAILED 는 핸들러가 최종 클릭 전에 멈춘 게 확실할 때만. 클릭이 나갔는지 모르는 중단
    # (크래시·예외)은 →INCIDENT — FAILED 는 "제출됐을 수 있음"을 숨긴다. 자동 재제출은 없다.
    S.SUBMITTING: frozenset({S.SUBMITTED, S.SUBMIT_MISMATCH, S.FAILED, S.INCIDENT}),
    S.SUBMIT_MISMATCH: frozenset({S.AWAITING_APPROVAL}),
    # 사람이 다시 시작할 때만. 자동 재시도는 JobRunner 가 FILLING→QUEUED 로 한다.
    S.FAILED: frozenset({S.QUEUED}),
    # 사람이 사이트에서 확인한 결과로만 닫는다 — 재시도 간선 없음.
    S.INCIDENT: frozenset({S.SUBMITTED}),
}

# 어느 상태에서든 cancel (§A3) — 이미 끝난 건은 제외.
TRANSITIONS: MappingProxyType[S, frozenset[S]] = MappingProxyType(
    {s: frozenset() if s in FINAL_STATES else _EDGES[s] | {S.CANCELLED} for s in S}
)


def allowed_targets(current: S) -> frozenset[S]:
    return TRANSITIONS[current]


def check_transition(current: S, to: S) -> None:
    if to not in TRANSITIONS[current]:
        raise InvalidTransition(current, to)


# 크래시·정지로 run 이 끊겼을 때 되돌릴 곳 (§A3, §A9). 대기 상태(NEEDS_*)는 그대로 둔다 —
# 사람이 마치면 재진입 run 으로 이어간다.
CRASH_RECOVERY: MappingProxyType[S, S] = MappingProxyType(
    {
        S.FILLING: S.QUEUED,
        S.REVISING: S.AWAITING_APPROVAL,
        S.SUBMITTING: S.INCIDENT,
    }
)

# 러너가 실패를 정리해 줄 상태 — 이 밖(검토 대기·사람 대기·종결)이면 핸들러가 이미 전이를 마쳤다.
_RUNNER_OWNED: frozenset[S] = frozenset({S.QUEUED, *CRASH_RECOVERY})

_FAILURE_STATE: dict[FailureKind, S] = {
    FailureKind.NEEDS_LOGIN: S.NEEDS_LOGIN,
    FailureKind.NEEDS_INPUT: S.NEEDS_INPUT,
    FailureKind.INCIDENT: S.INCIDENT,
}


def failure_target(current: S, failure: FailureKind, *, retry: bool = False) -> S | None:
    """핸들러 실패 뒤 지원 건이 갈 곳. None 이면 건드리지 않는다.

    `retry` 는 러너가 같은 job 을 다시 줄 세울 때 — 재시도 가능한 상태로만 되돌린다.
    제출 중(SUBMITTING)이면 어떤 실패든 INCIDENT 다(클릭이 나갔는지 모른다).
    표에 없는 목적지는 FAILED 로 떨어진다(QUEUED 에서 로그인 요구 등).
    """
    if failure is FailureKind.CONFLICT or current not in _RUNNER_OWNED:
        return None
    if current is S.SUBMITTING:
        return S.INCIDENT
    if retry:
        return CRASH_RECOVERY.get(current)
    target = _FAILURE_STATE.get(failure, S.FAILED)
    return target if target in TRANSITIONS[current] else S.FAILED
