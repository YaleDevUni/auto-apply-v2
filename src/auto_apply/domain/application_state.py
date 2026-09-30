"""지원 상태기계 전이 표 (§A3). 순수 함수 — 상태를 쓰는 건 `ApplicationService.transition()` 하나다.

표에 없는 전이는 `InvalidTransition`. 크래시 복구·재시도 간선(→QUEUED)도 여기 있어야 한다 —
JobRunner(§A9)는 이 표를 거쳐서만 지원 건을 되돌린다.
"""

from types import MappingProxyType

from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.errors import InvalidTransition

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
    # 제출 중 중단은 제출됐는지 모르므로 FAILED 로 닫고 자동으로 다시 제출하지 않는다.
    S.SUBMITTING: frozenset({S.SUBMITTED, S.SUBMIT_MISMATCH, S.FAILED}),
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
