"""로그인 벽·CAPTCHA·본인인증 휴리스틱 (domain/human_handoff.py, §A5 · 절대 규칙 3)."""

import pytest

from auto_apply.domain.human_handoff import (
    HandoffSignal,
    NodeFacts,
    detect_handoff,
    is_captcha_frame,
    is_captcha_label,
    is_captcha_node,
    is_login_url,
)

PAGE = "https://jobs.example.com/apply/42"
RECAPTCHA_ANCHOR = "https://www.google.com/recaptcha/api2/anchor?k=x&size=normal"
RECAPTCHA_V3 = "https://www.google.com/recaptcha/api2/anchor?k=x&size=invisible"
HCAPTCHA = "https://newassets.hcaptcha.com/captcha/v1/abc/static/hcaptcha.html#frame=checkbox"


def _field(name: str = "이름", **kw: object) -> NodeFacts:
    return NodeFacts(**{"role": "textbox", "name": name, "tag": "input", "frame_url": PAGE, **kw})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.saramin.co.kr/zf_user/auth?url=%2Fjobs",
        "https://example.com/login",
        "https://example.com/member/Login.html",
        "https://example.com/users/sign_in",
        "https://example.com/oauth2/authorize?client_id=1",
        "https://accounts.google.com/v3/signin/identifier",
        "https://nid.naver.com/nidlogin.login",
        "http://127.0.0.1:5000/sites/login_wall/login.html",
        "https://example.com/SSO/",
    ],
)
def test_login_urls(url):
    assert is_login_url(url)


@pytest.mark.parametrize(
    "url",
    [
        PAGE,
        "http://127.0.0.1:5000/sites/login_wall/",  # 지원 페이지 이름에 login 이 들어가도 아니다
        "https://example.com/blog/login-tips",
        "https://example.com/careers?next=/login",  # 쿼리는 보지 않는다
        "https://author.example.com/jobs",
        "about:blank",
        "",
    ],
)
def test_not_login_urls(url):
    assert not is_login_url(url)


def test_password_field_is_a_login_wall():
    nodes = [_field("아이디"), _field("비밀번호", input_type="password")]
    assert detect_handoff(PAGE, nodes) is HandoffSignal.PASSWORD_FIELD
    # type 을 text 로 바꾸는 "보기" 토글도 autocomplete 로 잡는다
    shown = [_field("비밀번호", input_type="TEXT", autocomplete="current-password")]
    assert detect_handoff(PAGE, shown) is HandoffSignal.PASSWORD_FIELD


def test_hidden_password_field_is_not_a_login_wall():
    nodes = [_field("이름"), _field("pw", input_type="password", hidden=True)]
    assert detect_handoff(PAGE, nodes) is None


def test_login_url_without_password_field():
    nodes = [NodeFacts(role="button", name="카카오로 로그인", tag="button")]
    assert detect_handoff("https://example.com/login", nodes) is HandoffSignal.LOGIN_URL
    # SSO 가 하위 프레임에 떠도 본다
    framed = [_field("이메일", frame_url="https://accounts.example.com/embed")]
    assert detect_handoff(PAGE, framed) is HandoffSignal.LOGIN_URL


def test_one_time_code_beats_password():
    nodes = [
        _field("비밀번호", input_type="password"),
        _field("인증번호", autocomplete="one-time-code"),
    ]
    assert detect_handoff(PAGE, nodes) is HandoffSignal.ONE_TIME_CODE


def test_plain_application_form_needs_nobody():
    nodes = [NodeFacts(role="heading", name="지원서", tag="h1"), _field("이름"), _field("이메일")]
    assert detect_handoff(PAGE, nodes) is None


@pytest.mark.parametrize(
    "node",
    [
        NodeFacts(role="checkbox", name="로봇이 아닙니다.", frame_url=RECAPTCHA_ANCHOR),
        NodeFacts(role="checkbox", name="hCaptcha 확인란", frame_url=HCAPTCHA),
        NodeFacts(role="text", name="I'm not a robot"),
        NodeFacts(role="text", name="Verify you are human"),
        NodeFacts(role="text", name="사람인지 확인하십시오."),
        _field("자동입력 방지 문자"),
        _field("보안 문자 입력"),
        _field("Captcha code"),
    ],
)
def test_captcha_is_detected_and_beats_everything(node):
    nodes = [_field("비밀번호", input_type="password"), node]
    assert detect_handoff("https://example.com/login", nodes) is HandoffSignal.CAPTCHA


def test_invisible_recaptcha_badge_is_not_a_challenge():
    # 보이지 않는 v3 배지는 폼마다 붙는다 — 링크·글자만 있으면 사람을 부르지 않는다
    badge = [
        NodeFacts(role="text", name="reCAPTCHA로 보호됨", frame_url=RECAPTCHA_V3),
        NodeFacts(role="link", name="개인정보처리방침", frame_url=RECAPTCHA_V3),
    ]
    assert detect_handoff(PAGE, [_field("이름"), *badge]) is None
    # 그래도 그 안의 요소는 에이전트가 건드리지 않는다
    assert all(is_captcha_node(n) for n in badge)


@pytest.mark.parametrize(
    ("url", "captcha"),
    [
        (RECAPTCHA_ANCHOR, True),
        ("https://www.recaptcha.net/recaptcha/api2/bframe?k=x", True),
        (HCAPTCHA, True),
        ("https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile", True),
        ("https://www.google.com/search?q=recaptcha", False),
        ("https://evilhcaptcha.com/", False),
        (PAGE, False),
    ],
)
def test_captcha_frames(url, captcha):
    assert is_captcha_frame(url) is captcha


@pytest.mark.parametrize(
    ("name", "captcha"),
    [
        ("자동입력방지", True),
        ("CAPTCHA", True),
        ("보안문자", True),
        ("이름", False),
        ("특수문자 포함", False),
    ],
)
def test_captcha_labels(name, captcha):
    assert is_captcha_label(name) is captcha


def test_hidden_captcha_text_is_not_a_challenge():
    assert (
        detect_handoff(PAGE, [NodeFacts(role="text", name="로봇이 아닙니다", hidden=True)]) is None
    )
