"""실패 분류 = 재시도 정책 (§A9). 재시도는 인프라성 실패만, 도메인 실패는 재시도 없이 상태 전이.

기대 표는 §A9 를 손으로 옮긴 것이다. 새 에러 클래스가 생기면 이 표에 자리를 정해야 한다 —
`test_every_error_class_is_classified` 가 빠뜨린 것을 잡는다.
"""

import pytest

from auto_apply.domain import errors as E
from auto_apply.domain.failure import FailureKind as F
from auto_apply.domain.failure import RetryPolicy, classify_failure

_EXPECTED: dict[type[BaseException], F] = {
    E.BrowserLaunchFailed: F.TRANSIENT,
    E.LLMExecutionError: F.TRANSIENT,
    E.LLMAuthRequired: F.FATAL,
    E.LLMQuotaExceeded: F.FATAL,
    E.LLMSchemaViolation: F.FATAL,
    E.AuthRequired: F.NEEDS_LOGIN,
    E.CaptchaEncountered: F.NEEDS_INPUT,
    E.SubmitIncident: F.INCIDENT,
    E.InvalidTransition: F.CONFLICT,
    E.SubmitGuardUnavailable: F.FATAL,  # 하네스 없이 되풀이하지 않는다(닫힌 쪽)
    E.PolicyViolation: F.FATAL,
    E.ChromeNotFound: F.FATAL,
    E.BrowserProfileInUse: F.FATAL,
    E.TerminalError: F.FATAL,
    E.ProfileNotFound: F.FATAL,
    E.NotFound: F.FATAL,
    E.InvalidInput: F.FATAL,
    E.PageActionFailed: F.FATAL,
    E.TextExtractionFailed: F.FATAL,
    E.BlobNotFound: F.FATAL,
    E.UniqueIdentifierRejected: F.FATAL,
    E.AnswerKeyConflict: F.FATAL,
    E.UploadRejected: F.FATAL,
    E.AutoApplyError: F.FATAL,
    # 어댑터가 감싸지 않고 새어 나온 예외 — 인프라성인지 모르니 되풀이하지 않는다.
    RuntimeError: F.FATAL,
    TimeoutError: F.FATAL,
    ValueError: F.FATAL,
}


def _make(cls: type[BaseException]) -> BaseException:
    if cls is E.InvalidTransition:
        return E.InvalidTransition("filling", "draft")
    if cls is E.UploadRejected:
        return E.UploadRejected("empty", "x")
    if cls is E.PageActionFailed:
        return E.PageActionFailed(E.PageFailure.TIMEOUT, "x")
    return cls("x")


@pytest.mark.parametrize(("cls", "expected"), list(_EXPECTED.items()), ids=lambda v: str(v))
def test_classification_table(cls, expected):
    assert classify_failure(_make(cls)) is expected


def test_every_error_class_is_classified():
    found: set[type] = set()
    pending: list[type] = [E.AutoApplyError]
    while pending:
        cls = pending.pop()
        found.add(cls)
        pending.extend(cls.__subclasses__())
    missing = {c.__name__ for c in found if c.__module__ == E.__name__} - {
        c.__name__ for c in _EXPECTED
    }
    assert not missing, f"{sorted(missing)} 의 재시도 분류를 표에 정하라 (domain/errors.py)"


def test_only_infra_failures_are_transient():
    assert {c for c, f in _EXPECTED.items() if f is F.TRANSIENT} == {
        E.BrowserLaunchFailed,
        E.LLMExecutionError,
    }


def test_terminal_errors_never_retry():
    for cls in _EXPECTED:
        if issubclass(cls, E.TerminalError):
            assert classify_failure(_make(cls)) is not F.TRANSIENT, cls


def test_retry_policy_backoff_is_exponential_and_capped():
    p = RetryPolicy(max_attempts=4, base_delay_s=2, factor=3, max_delay_s=10)
    assert [p.delay_s(a) for a in (1, 2, 3)] == [2, 6, 10]
    assert [p.should_retry(a) for a in (1, 2, 3, 4, 5)] == [True, True, True, False, False]


def test_default_retry_policy_is_bounded():
    p = RetryPolicy()
    assert p.max_attempts >= 1
    assert not p.should_retry(p.max_attempts)
    assert p.delay_s(100) == p.max_delay_s
