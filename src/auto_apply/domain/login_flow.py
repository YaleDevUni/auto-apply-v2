"""자동 로그인 시도의 결과를 판정하는 순수 함수 — `scripts/auto_login.py`가 쓴다.

CAPTCHA/추가 인증(SMS 등)은 여기서 감지만 하고 절대 우회하지 않는다(CLAUDE.md 하지 말 것).
호출자는 CAPTCHA 판정을 받으면 즉시 실패시키고 사람에게 넘겨야 한다. 판정 로직을 여기 순수
함수로 뺀 이유는 이 안전장치(§ "자동 로그인 정책" ARCHITECTURE.md)가 실제로 동작하는지
Playwright/실제 사이트 없이 고정 HTML로 테스트하기 위해서다.
"""

from enum import StrEnum


class LoginOutcome(StrEnum):
    SUCCESS = "success"
    CAPTCHA = "captcha"
    INVALID_CREDENTIALS = "invalid_credentials"
    UNKNOWN = "unknown"


# 대소문자 섞여 나올 수 있어 소문자 비교. 사이트마다 문구가 다를 수 있어 새 플랫폼을 붙일 때마다
# 실측해서 추가한다.
_CAPTCHA_MARKERS = ("recaptcha", "hcaptcha", "g-recaptcha", "자동입력 방지", "보안문자", "captcha")
_INVALID_CREDENTIAL_MARKERS = (
    "아이디 또는 비밀번호",
    "일치하지 않습니다",
    "비밀번호가 틀렸습니다",
    "존재하지 않는 아이디",
)


def detect_login_outcome(*, still_on_login_page: bool, html: str) -> LoginOutcome:
    """로그인 폼 제출 뒤 페이지 상태로 결과를 판정한다.

    CAPTCHA 마커는 로그인 성공 여부와 무관하게 최우선으로 본다 — 로그인 페이지에 CAPTCHA가
    끼어 있으면(제출 전이든 후든) 사람에게 넘겨야 하는 상태이기 때문이다.
    """
    lowered = html.lower()
    if any(marker.lower() in lowered for marker in _CAPTCHA_MARKERS):
        return LoginOutcome.CAPTCHA
    if not still_on_login_page:
        return LoginOutcome.SUCCESS
    if any(marker in html for marker in _INVALID_CREDENTIAL_MARKERS):
        return LoginOutcome.INVALID_CREDENTIALS
    return LoginOutcome.UNKNOWN
