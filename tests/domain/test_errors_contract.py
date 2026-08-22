"""`TerminalError` 서브클래스는 전부 `NON_RETRYABLE` 에 있어야 한다.

`TerminalError` 의 정의가 곧 "재시도해도 결과가 같다"인데, Temporal 은 클래스 계층을 모르고
`non_retryable_error_types` 에 실린 **이름 문자열**만 본다(§5). 그래서 새 TerminalError 를
만들고 이 튜플에 등록하는 걸 잊으면 조용히 `maximum_attempts` 만큼(대부분 5회) 같은 실패를
반복한 뒤에야 끝난다 — 에러도 안 나고 로그만 길어져서 알아채기 어렵다. 실제로 이 프로젝트에서
이미 두 번(등록 안 된 플랫폼 URL, AuthRequired) 라이브에서 실측하고 사후에 고쳤다
(`workflows/application.py`/`_execution.py` 의 `_QUICK` 주석).

등록을 사람의 기억이 아니라 테스트로 강제한다 — 규칙을 더 늘리는 게 아니라, 이미 있는 규칙이
지켜졌는지 기계가 보게 하는 쪽이다.
"""

from auto_apply.domain import errors


def _terminal_subclasses() -> set[str]:
    found: set[str] = set()
    pending = [errors.TerminalError]
    while pending:
        for sub in pending.pop().__subclasses__():
            found.add(sub.__name__)
            pending.append(sub)
    return found


def test_every_terminal_error_is_registered_as_non_retryable():
    missing = _terminal_subclasses() - set(errors.NON_RETRYABLE)

    assert not missing, (
        f"{sorted(missing)} 가 NON_RETRYABLE 에 없다 — Temporal 이 이름으로만 판단하므로"
        " 등록 전까지는 재시도해도 소용없는 실패를 5회씩 반복한다 (domain/errors.py)."
    )


def test_non_retryable_has_no_dangling_names():
    """튜플에만 남고 클래스는 사라진 이름(오타/삭제)을 잡는다.

    이름 문자열이라 안 잡히면 조용히 아무 데도 안 걸린다.
    """
    known = {
        name
        for name in dir(errors)
        if isinstance(getattr(errors, name), type)
        and issubclass(getattr(errors, name), errors.AutoApplyError)
    }

    assert set(errors.NON_RETRYABLE) <= known
