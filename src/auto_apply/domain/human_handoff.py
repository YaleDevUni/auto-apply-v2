"""사람에게 넘길 화면인가 — 로그인 벽·CAPTCHA·본인인증 휴리스틱 (§A5, 절대 규칙 3). 순수 함수.

앱은 비밀번호를 치지 않고 CAPTCHA·본인인증을 풀지 않는다. 이 규칙은 snapshot 에 "사람 몫" 신호를
붙여 에이전트를 request_login·request_human 으로 이끌고, CAPTCHA 위젯을 에이전트가 건드리지
못하게 한다. 오탐은 사람을 한 번 더 부르는 비용이고 미탐은 에이전트가 CAPTCHA 를 누르는
사고라 CAPTCHA 쪽은 넓게 본다.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from auto_apply.domain.page_elements import input_type


class HandoffSignal(StrEnum):
    CAPTCHA = "captcha"  # → request_human
    ONE_TIME_CODE = "one_time_code"  # SMS·본인인증 코드 칸 → request_human
    PASSWORD_FIELD = "password_field"  # → request_login
    LOGIN_URL = "login_url"  # → request_login


LOGIN_SIGNALS = frozenset({HandoffSignal.PASSWORD_FIELD, HandoffSignal.LOGIN_URL})


@dataclass(frozen=True, slots=True)
class NodeFacts:
    """snapshot 한 줄에서 휴리스틱이 보는 것 (contracts 에 기대지 않게 원시값으로)."""

    role: str
    name: str = ""
    tag: str = ""
    input_type: str | None = None
    autocomplete: str | None = None
    frame_url: str = ""
    hidden: bool = False


# 경로 조각(확장자 뺀 것)이 정확히 이것이면 로그인 화면. 접두 일치는 하지 않는다 — "login_wall"
# 같은 지원 페이지 이름을 로그인 화면으로 오인하지 않게. 놓친 것은 비밀번호 칸이 잡는다.
_LOGIN_SEGMENTS = frozenset(
    {
        "login", "log-in", "log_in", "logon", "signin", "sign-in", "sign_in", "auth",
        "authorize", "authentication", "sso", "oauth", "oauth2", "saml",
    }
)  # fmt: skip
_LOGIN_HOST_LABELS = frozenset({"accounts", "login", "auth", "sso", "signin", "nid", "appleid"})
_PAGE_EXTENSIONS = frozenset({".html", ".htm", ".do", ".php", ".asp", ".aspx", ".jsp"})

# CAPTCHA 제공자 프레임. 이 안의 요소는 에이전트가 건드리지 않는다(보이지 않는 v3 배지 포함).
_CAPTCHA_HOSTS = ("hcaptcha.com", "challenges.cloudflare.com", "arkoselabs.com",
                  "funcaptcha.com", "geetest.com")  # fmt: skip
_RECAPTCHA_HOSTS = ("google.com", "recaptcha.net", "gstatic.com")
# 보이는 글자로 CAPTCHA 를 알아보는 어휘 — 공백·문장부호를 뺀 소문자로 비교한다.
# 맨 "captcha" 는 넣지 않는다: 보이지 않는 reCAPTCHA v3 배지("reCAPTCHA 로 보호됨")가 어디에나 있다.
_CAPTCHA_PHRASES = (
    "로봇이아닙니다", "자동입력방지", "보안문자", "imnotarobot", "iamnotarobot",
    "verifyyouarehuman", "verifythatyouarehuman", "사람인지확인",
)  # fmt: skip
# 칸 이름으로 보는 어휘 — 이미지 CAPTCHA 의 답 칸. 여기엔 "captcha" 도 넣는다.
_CAPTCHA_FIELD_WORDS = (*_CAPTCHA_PHRASES, "captcha")
_CHALLENGE_ROLES = frozenset({"checkbox", "button", "textbox"})
_SQUASH = re.compile(r"[\W_]+", re.UNICODE)


def _squash(text: str) -> str:
    return _SQUASH.sub("", unicodedata.normalize("NFKC", text).casefold())


def _host(url: str) -> str:
    return (urlsplit(url.strip()).hostname or "").lower().rstrip(".")


def _under(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_login_url(url: str) -> bool:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.split(".", 1)[0] in _LOGIN_HOST_LABELS and "." in host:
        return True
    for segment in parts.path.lower().split("/"):
        path = PurePosixPath(segment)
        stem = path.stem if path.suffix in _PAGE_EXTENSIONS else segment
        if stem in _LOGIN_SEGMENTS:
            return True
    return False


def is_captcha_frame(url: str) -> bool:
    """CAPTCHA 제공자의 프레임인가 (위젯·도전 창·배지)."""
    host = _host(url)
    if any(_under(host, d) for d in _CAPTCHA_HOSTS):
        return True
    return any(_under(host, d) for d in _RECAPTCHA_HOSTS) and "/recaptcha/" in urlsplit(url).path


def is_captcha_label(name: str) -> bool:
    """이름이 CAPTCHA 답 칸·위젯으로 보이는가 — 이런 칸은 에이전트가 채우지 않는다."""
    squashed = _squash(name)
    return any(w in squashed for w in _CAPTCHA_FIELD_WORDS)


def is_captcha_node(node: NodeFacts) -> bool:
    """에이전트가 건드리면 CAPTCHA 를 푸는 셈인 요소 (절대 규칙 3)."""
    return is_captcha_frame(node.frame_url) or is_captcha_label(node.name)


def _is_captcha_challenge(node: NodeFacts) -> bool:
    if node.hidden:
        return False
    if is_captcha_frame(node.frame_url) and node.role in _CHALLENGE_ROLES:
        return True
    squashed = _squash(node.name)
    if any(p in squashed for p in _CAPTCHA_PHRASES):
        return True
    return node.role == "textbox" and is_captcha_label(node.name)


def detect_handoff(page_url: str, nodes: Iterable[NodeFacts]) -> HandoffSignal | None:
    """지금 화면이 사람 몫이면 그 근거 — CAPTCHA > 인증 코드 > 비밀번호 칸 > 로그인 주소 순.

    보이지 않는 칸은 보지 않는다(자동 완성 함정용 숨은 비밀번호 칸이 흔하다).
    """
    found: set[HandoffSignal] = set()
    frame_urls = {page_url}
    for node in nodes:
        frame_urls.add(node.frame_url)
        if _is_captcha_challenge(node):
            return HandoffSignal.CAPTCHA
        if node.hidden:
            continue
        tokens = set((node.autocomplete or "").lower().split())
        if "one-time-code" in tokens:
            found.add(HandoffSignal.ONE_TIME_CODE)
        elif input_type(node.tag, node.input_type) == "password" or tokens & {
            "current-password",
            "new-password",
        }:
            found.add(HandoffSignal.PASSWORD_FIELD)
    if any(is_login_url(u) for u in frame_urls if u):
        found.add(HandoffSignal.LOGIN_URL)
    return next((s for s in HandoffSignal if s in found), None)
