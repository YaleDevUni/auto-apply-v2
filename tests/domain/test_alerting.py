"""domain/alerting.py — "성공으로 끝나는 실패"를 알림 문구로 바꾸는 규칙.

여기 걸린 조건들이 곧 "언제 텔레그램이 울리는가"라 순수 함수로 고정해둔다 — Temporal 없이
조건만 바꿔가며 검증할 수 있어야 나중에 임계치를 조정할 때 안전하다.
"""

from auto_apply.contracts.job import PlatformCollectionResult
from auto_apply.domain.alerting import ENRICH_ERROR_ALERT_MIN, collection_alert, intake_alert


def _ok(platform: str = "wanted", **kw: int) -> PlatformCollectionResult:
    base = {"found": 10, "passed": 5, "actionable": 3}
    return PlatformCollectionResult(platform=platform, **{**base, **kw})


def test_healthy_collection_is_silent():
    assert collection_alert([_ok("wanted"), _ok("saramin")]) is None


def test_platform_error_is_reported_even_though_the_workflow_succeeded():
    results = [_ok("wanted"), PlatformCollectionResult(platform="saramin", error="AuthRequired: x")]

    message = collection_alert(results)

    assert message is not None
    assert "saramin" in message
    assert "AuthRequired" in message
    assert "wanted 3건" in message, "멀쩡한 플랫폼 결과도 같이 보여줘야 판단이 된다"


def test_zero_found_without_error_is_the_quietest_failure():
    """예외가 안 나므로 워크플로우도 watchdog 도 정상으로 본다 — 여기서만 잡힌다."""
    message = collection_alert([_ok("wanted", found=0, passed=0, actionable=0)])

    assert message is not None
    assert "0건 수집" in message


def test_scattered_enrich_errors_do_not_alert_but_a_pile_does():
    assert collection_alert([_ok(enrich_errors=ENRICH_ERROR_ALERT_MIN - 1)]) is None

    message = collection_alert([_ok(enrich_errors=ENRICH_ERROR_ALERT_MIN)])

    assert message is not None
    assert "상세 조회" in message


def test_no_platforms_at_all_is_reported():
    assert collection_alert([]) is not None


def test_intake_is_silent_when_something_actually_started():
    assert intake_alert(started=2, skipped=5, candidates=7) is None


def test_intake_zero_candidates_points_at_the_upstream_cache():
    message = intake_alert(started=0, skipped=0, candidates=0)

    assert message is not None
    assert "후보 공고가 0건" in message


def test_intake_all_filtered_out_says_so_differently():
    message = intake_alert(started=0, skipped=4, candidates=4)

    assert message is not None
    assert "후보 4건" in message
    assert "제외 4건" in message
