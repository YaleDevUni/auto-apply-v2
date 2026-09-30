"""전이 표 전 경로 (§A3) — 상태 두 개의 모든 순서쌍을 허용·금지로 대조한다.

기대 표는 §A3 표를 손으로 옮긴 것이다. 코드의 표를 그대로 복사해 비교하면 둘이 같이 틀려도
통과한다.
"""

import itertools

import pytest

from auto_apply.domain.application_state import (
    CRASH_RECOVERY,
    FINAL_STATES,
    INITIAL_STATE,
    TRANSITIONS,
    check_transition,
    failure_target,
)
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.errors import InvalidTransition
from auto_apply.domain.failure import FailureKind, classify_failure

_EXPECTED_EDGES = {
    (S.DRAFT, S.QUEUED),
    (S.QUEUED, S.FILLING),
    (S.QUEUED, S.FAILED),
    (S.FILLING, S.AWAITING_APPROVAL),
    (S.FILLING, S.NEEDS_INPUT),
    (S.FILLING, S.NEEDS_LOGIN),
    (S.FILLING, S.FAILED),
    (S.FILLING, S.INCIDENT),
    (S.FILLING, S.QUEUED),
    (S.NEEDS_INPUT, S.FILLING),
    (S.NEEDS_INPUT, S.QUEUED),
    (S.NEEDS_LOGIN, S.FILLING),
    (S.NEEDS_LOGIN, S.QUEUED),
    (S.AWAITING_APPROVAL, S.SUBMITTING),
    (S.AWAITING_APPROVAL, S.REVISING),
    (S.AWAITING_APPROVAL, S.REJECTED),
    (S.REVISING, S.AWAITING_APPROVAL),
    (S.REVISING, S.FAILED),
    (S.REVISING, S.INCIDENT),
    (S.SUBMITTING, S.SUBMITTED),
    (S.SUBMITTING, S.SUBMIT_MISMATCH),
    (S.SUBMITTING, S.FAILED),
    (S.SUBMITTING, S.INCIDENT),
    (S.SUBMIT_MISMATCH, S.AWAITING_APPROVAL),
    (S.FAILED, S.QUEUED),
    (S.INCIDENT, S.SUBMITTED),
} | {(s, S.CANCELLED) for s in S if s not in {S.SUBMITTED, S.REJECTED, S.CANCELLED}}

_ALL_PAIRS = list(itertools.product(S, S))


@pytest.mark.parametrize(("current", "to"), [p for p in _ALL_PAIRS if p in _EXPECTED_EDGES])
def test_allowed(current, to):
    check_transition(current, to)


@pytest.mark.parametrize(("current", "to"), [p for p in _ALL_PAIRS if p not in _EXPECTED_EDGES])
def test_forbidden(current, to):
    with pytest.raises(InvalidTransition) as exc:
        check_transition(current, to)
    assert (exc.value.current, exc.value.to) == (current, to)


def test_table_covers_every_state():
    assert set(TRANSITIONS) == set(S)


def test_final_states_have_no_exit():
    assert {S.SUBMITTED, S.REJECTED, S.CANCELLED} == FINAL_STATES
    assert all(not TRANSITIONS[s] for s in FINAL_STATES)


def test_incident_closes_only_by_human_outcome():
    """INCIDENT 는 자동 재시도 없이 사람이 SUBMITTED·CANCELLED 로만 닫는다 (§A4 L5)."""
    assert TRANSITIONS[S.INCIDENT] == {S.SUBMITTED, S.CANCELLED}


def test_only_approval_path_reaches_submitting():
    """제출 단계로 가는 간선은 승인 대기에서 하나뿐이다 (절대 규칙 1 — run 이 건너뛸 수 없다)."""
    assert {c for c, t in _EXPECTED_EDGES if t is S.SUBMITTING} == {S.AWAITING_APPROVAL}
    assert {c for c in S if S.SUBMITTING in TRANSITIONS[c]} == {S.AWAITING_APPROVAL}


def test_every_state_is_reachable_from_initial():
    seen, frontier = {INITIAL_STATE}, [INITIAL_STATE]
    while frontier:
        for nxt in TRANSITIONS[frontier.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(S)


def test_table_is_read_only():
    with pytest.raises(TypeError):
        TRANSITIONS[S.DRAFT] = frozenset({S.SUBMITTED})  # type: ignore[index]


def test_invalid_transition_is_not_retried():
    assert classify_failure(InvalidTransition(S.DRAFT, S.SUBMITTED)) is FailureKind.CONFLICT


def test_crash_recovery_targets():
    """§A3: 제출 중 중단은 INCIDENT — 클릭이 나갔는지 모르므로 FAILED 로 숨기지 않는다."""
    assert dict(CRASH_RECOVERY) == {
        S.FILLING: S.QUEUED,
        S.REVISING: S.AWAITING_APPROVAL,
        S.SUBMITTING: S.INCIDENT,
    }
    assert all(to in TRANSITIONS[cur] for cur, to in CRASH_RECOVERY.items())


_F = FailureKind
_FAILURE_TABLE = [
    # (현재, 실패, retry) -> 목적지
    (S.FILLING, _F.TRANSIENT, True, S.QUEUED),
    (S.QUEUED, _F.TRANSIENT, True, None),
    (S.REVISING, _F.TRANSIENT, True, S.AWAITING_APPROVAL),
    (S.FILLING, _F.TRANSIENT, False, S.FAILED),  # 재시도 한도 소진
    (S.FILLING, _F.NEEDS_LOGIN, False, S.NEEDS_LOGIN),
    (S.FILLING, _F.NEEDS_INPUT, False, S.NEEDS_INPUT),
    (S.FILLING, _F.INCIDENT, False, S.INCIDENT),
    (S.FILLING, _F.FATAL, False, S.FAILED),
    (S.QUEUED, _F.FATAL, False, S.FAILED),  # 핸들러가 시작도 못 함
    (S.QUEUED, _F.NEEDS_LOGIN, False, S.FAILED),  # 표에 없는 목적지는 FAILED 로
    (S.REVISING, _F.NEEDS_LOGIN, False, S.FAILED),
    (S.REVISING, _F.INCIDENT, False, S.INCIDENT),
    # 제출 중이면 무엇이든 INCIDENT — 재시도로 되돌리지도 않는다.
    (S.SUBMITTING, _F.TRANSIENT, True, S.INCIDENT),
    (S.SUBMITTING, _F.FATAL, False, S.INCIDENT),
    (S.SUBMITTING, _F.NEEDS_LOGIN, False, S.INCIDENT),
    # 그 사이 다른 쪽이 바꿨거나(CONFLICT) 핸들러가 이미 정리한 상태면 건드리지 않는다.
    (S.FILLING, _F.CONFLICT, False, None),
    (S.SUBMITTING, _F.CONFLICT, False, None),
    (S.AWAITING_APPROVAL, _F.FATAL, False, None),
    (S.NEEDS_LOGIN, _F.FATAL, False, None),
    (S.CANCELLED, _F.FATAL, False, None),
    (S.INCIDENT, _F.TRANSIENT, True, None),
]


@pytest.mark.parametrize(("current", "failure", "retry", "expected"), _FAILURE_TABLE)
def test_failure_target(current, failure, retry, expected):
    target = failure_target(current, failure, retry=retry)
    assert target is expected
    if target is not None:
        check_transition(current, target)
