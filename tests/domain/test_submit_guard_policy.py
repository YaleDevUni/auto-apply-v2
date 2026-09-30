"""SubmitGuard 결정 규칙 (§A4 L3·L4·L5, D17) — 요청 판정 표, 대화상자, 새로 나타난 완료 근거."""

import pytest

from auto_apply.contracts.click import PageText
from auto_apply.domain.submit_guard_policy import (
    BlockReason,
    GuardMode,
    StepLanding,
    carried_values,
    carries_input,
    completion_evidence,
    dialog_verdict,
    request_verdict,
    step_landing,
)
from auto_apply.domain.url_policy import matches_origin

S, R, P = GuardMode.STRICT, GuardMode.RELAXED, GuardMode.STEP
APP = ("http://127.0.0.1:8000",)
SITE = "https://jobs.example.com"


def _v(mode, method, url, nav=False, carried=(), forbidden=APP):
    return request_verdict(
        mode=mode, method=method, url=url, navigation=nav, carried=carried,
        forbidden_origins=forbidden,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("mode", "method", "url", "nav", "expected"),
    [
        # 문서 POST(폼 제출)는 모드와 관계없이 막는다
        (R, "POST", f"{SITE}/apply", True, BlockReason.DOCUMENT_POST),
        (S, "POST", f"{SITE}/apply", True, BlockReason.DOCUMENT_POST),
        (R, "put", f"{SITE}/apply", True, BlockReason.DOCUMENT_POST),
        # relaxed: 단계 저장·업로드 fetch/xhr/beacon 은 통과
        (R, "POST", f"{SITE}/api/save", False, None),
        (R, "PATCH", f"{SITE}/api/save", False, None),
        (R, "GET", f"{SITE}/apply?name=x", True, None),
        # strict: 비-GET 전부, 알 수 없는 method 도 비-GET 으로
        (S, "POST", f"{SITE}/api/save", False, BlockReason.STRICT_NON_GET),
        (S, "OPTIONS", f"{SITE}/api/save", False, BlockReason.STRICT_NON_GET),
        (S, "BREW", f"{SITE}/api/save", False, BlockReason.STRICT_NON_GET),
        (S, "", f"{SITE}/api/save", False, BlockReason.STRICT_NON_GET),
        # strict: 쿼리를 싣는 문서 탐색은 GET 제출이다 — 단순 페이지 이동은 통과
        (S, "GET", f"{SITE}/apply?job=1", True, BlockReason.STRICT_GET_QUERY),
        (S, "GET", f"{SITE}/apply/form.html", True, None),
        (S, "GET", f"{SITE}/apply/form.html#top", True, None),
        (S, "HEAD", f"{SITE}/x", False, None),
        (S, "GET", f"{SITE}/api/config?lang=ko", False, None),  # 하위 자원 GET 은 통과
        # 앱 출처는 가드가 꺼져 있어도(mode=None) 막는다 (T2.4 이관)
        (None, "GET", "http://localhost:8000/api/session", False, BlockReason.FORBIDDEN_ORIGIN),
        (None, "GET", "http://127.0.0.2:8000/", True, BlockReason.FORBIDDEN_ORIGIN),
        (R, "GET", "http://[::1]:8000/", True, BlockReason.FORBIDDEN_ORIGIN),
        (None, "POST", f"{SITE}/apply", True, None),  # run 밖(사람) — 앱 출처만
        (None, "GET", "http://127.0.0.1:8001/", True, None),
        (S, "GET", "data:text/html,<b>x</b>", True, None),
    ],
)
def test_request_verdict_table(mode, method, url, nav, expected):
    assert _v(mode, method, url, nav) is expected


def test_document_navigation_carrying_typed_values_is_blocked_in_every_mode_but_step():
    # 입력값을 실은 문서 탐색 = GET 폼 제출. step 창은 단계 이동 버튼의 폼 제출(GET 폼 포함)을
    # 통과시키는 창이라 예외다 — 결과 화면은 SubmitGuard.after_step 이 본다 (D17)
    carried = carried_values(["홍길동"])
    url = f"{SITE}/apply?name=%ED%99%8D%EA%B8%B8%EB%8F%99"
    assert _v(S, "GET", url, nav=True, carried=carried) is BlockReason.STRICT_GET_QUERY
    assert _v(P, "GET", url, nav=True, carried=carried) is None


@pytest.mark.parametrize(
    ("method", "url", "nav", "expected"),
    [
        # 단계 이동 버튼의 폼 제출 — 문서 탐색은 method 와 관계없이 통과
        ("POST", f"{SITE}/apply/step1", True, None),
        ("PUT", f"{SITE}/apply/step1", True, None),
        ("GET", f"{SITE}/apply?step=2", True, None),
        # 그 밖은 relaxed 와 같다
        ("POST", f"{SITE}/api/save", False, None),
        ("GET", f"{SITE}/api/config?lang=ko", False, None),
        # 앱 출처는 step 창에서도 막는다
        ("POST", "http://127.0.0.1:8000/api/approve", True, BlockReason.FORBIDDEN_ORIGIN),
        ("POST", "http://localhost:8000/api/approve", False, BlockReason.FORBIDDEN_ORIGIN),
    ],
)
def test_step_window_passes_document_navigation_only(method, url, nav, expected):
    assert _v(P, method, url, nav) is expected


def test_strict_get_carrying_typed_values_is_blocked():
    carried = carried_values(["홍길동", "hong@example.com", "예", "3"])
    assert carried == ("hong@example.com", "홍길동")  # 짧은 값은 어디에나 있어 보지 않는다
    url = f"{SITE}/api/check?n=%ED%99%8D%EA%B8%B8%EB%8F%99"
    assert _v(S, "GET", url, carried=carried) is BlockReason.CARRIES_INPUT
    assert _v(S, "GET", f"{SITE}/x/HONG@EXAMPLE.COM", carried=carried) is (
        BlockReason.CARRIES_INPUT
    )
    assert _v(R, "GET", url, carried=carried) is None  # relaxed 요청(자동 완성)은 통과
    assert _v(S, "GET", f"{SITE}/api/check?n=kim", carried=carried) is None


def test_document_navigation_carrying_typed_values_is_blocked_in_every_mode():
    # 입력값을 실은 문서 탐색 = GET 폼 제출 — 페이지 스크립트 층이 비껴가져도 네트워크 층이 막는다
    carried = carried_values(["홍길동"])
    url = f"{SITE}/apply?name=%ED%99%8D%EA%B8%B8%EB%8F%99"
    assert _v(R, "GET", url, nav=True, carried=carried) is BlockReason.CARRIES_INPUT
    assert _v(R, "GET", f"{SITE}/a?rrn=900101-1234567", nav=True) is BlockReason.CARRIES_INPUT
    assert _v(R, "GET", f"{SITE}/apply?name=kim", nav=True, carried=carried) is None
    assert _v(None, "GET", url, nav=True, carried=carried) is None  # run 밖(사람)


def test_resident_number_in_url_counts_as_carried_input():
    assert carries_input(f"{SITE}/a?rrn=900101-1234567", ())
    assert carries_input(f"{SITE}/a?rrn=9001011234567", ())
    assert not carries_input(f"{SITE}/a?id=12345", ())


def test_matches_origin_ignores_non_web_urls():
    assert matches_origin("http://LOCALHOST:8000/x", APP)
    assert not matches_origin("blob:http://127.0.0.1:8000/x", APP)
    assert not matches_origin("not a url", APP)


@pytest.mark.parametrize(
    ("kind", "verdict"),
    [
        ("alert", "accept"),
        ("confirm", "dismiss"),
        ("prompt", "dismiss"),
        ("beforeunload", "dismiss"),
        ("CONFIRM", "dismiss"),
        ("unknown", "dismiss"),
    ],
)
def test_dialogs_are_declined_except_alert(kind, verdict):
    assert dialog_verdict(kind) == verdict


def test_completion_only_counts_new_text():
    before = ["지원서", "지원이 완료되었습니다"]  # 원래 있던 문구(예: 안내 배너)
    assert completion_evidence([SITE], before, [SITE], [*before, "이름"]) is None
    after = [*before, "지원해주셔서 감사합니다"]
    assert completion_evidence([SITE], before, [SITE], after) is not None


def test_completion_on_new_page_and_new_frame_url():
    assert completion_evidence([SITE], ["지원서"], [SITE], ["지원이 완료되었습니다"])
    urls = [SITE, f"{SITE}/ats/thanks"]
    assert completion_evidence([SITE], [], urls, []) == "url:thanks"
    assert completion_evidence(urls, [], urls, []) is None  # 원래 있던 프레임 URL


def test_completion_across_line_break_is_caught():
    assert completion_evidence([], [], [], ["지원이", "완료되었습니다"]) is not None


_FORM = PageText(urls=(f"{SITE}/apply",), lines=("지원서", "이름", "경력", "자기소개"), inputs=3)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # 새 주소의 입력 화면 — 다음 단계
        (PageText(urls=(f"{SITE}/apply/2",), lines=("2단계",), inputs=1), StepLanding.NEXT_STEP),
        # 같은 주소(폼 POST 가 같은 URL 에 2단계를 그린다)라도 전 화면 글자가 절반 넘게 사라졌다
        (_FORM.model_copy(update={"lines": ("지원서", "학력", "어학")}), StepLanding.NEXT_STEP),
        # 입력칸 없는 검토 페이지 — 제출 버튼이 남아 있으면 아직 제출 전
        (PageText(urls=(f"{SITE}/review",), lines=("이름: 홍길동",), submitters=1),
         StepLanding.NEXT_STEP),
        # 검증 오류 한 줄이 더해졌을 뿐 — 같은 화면
        (_FORM.model_copy(update={"lines": (*_FORM.lines, "이름을 입력하세요")}),
         StepLanding.SAME_PAGE),
        # 입력칸도 제출 버튼도 없다 — 제출됐는지 모른다
        (PageText(urls=(f"{SITE}/receipt",), lines=("접수 번호 1234",)), StepLanding.UNCLEAR),
        (PageText(urls=(f"{SITE}/apply",), lines=_FORM.lines), StepLanding.UNCLEAR),
    ],
)  # fmt: skip
def test_step_landing(now, expected):
    assert step_landing(_FORM, now) is expected


def test_step_landing_without_a_prior_observation_counts_as_moved():
    assert step_landing(None, _FORM) is StepLanding.NEXT_STEP
