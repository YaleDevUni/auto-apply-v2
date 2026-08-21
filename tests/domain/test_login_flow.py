from auto_apply.domain.login_flow import LoginOutcome, detect_login_outcome


def test_success_when_navigated_away_from_login_page() -> None:
    outcome = detect_login_outcome(still_on_login_page=False, html="<html>입사지원 현황</html>")
    assert outcome is LoginOutcome.SUCCESS


def test_captcha_takes_priority_even_after_navigating_away() -> None:
    # 일부 사이트는 CAPTCHA 를 로그인 완료 뒤 별도 단계(추가 인증)로 붙이기도 한다 —
    # "로그인 페이지를 벗어났다"만으로 안전하다고 판단하면 안 된다.
    html = '<div class="g-recaptcha"></div>'
    outcome = detect_login_outcome(still_on_login_page=False, html=html)
    assert outcome is LoginOutcome.CAPTCHA


def test_captcha_detected_while_still_on_login_page() -> None:
    html = "<script>자동입력 방지문자를 입력해주세요</script>"
    outcome = detect_login_outcome(still_on_login_page=True, html=html)
    assert outcome is LoginOutcome.CAPTCHA


def test_invalid_credentials_when_still_on_login_page_with_error_text() -> None:
    html = "<div class='error'>아이디 또는 비밀번호가 일치하지 않습니다</div>"
    outcome = detect_login_outcome(still_on_login_page=True, html=html)
    assert outcome is LoginOutcome.INVALID_CREDENTIALS


def test_unknown_when_still_on_login_page_without_recognizable_marker() -> None:
    outcome = detect_login_outcome(still_on_login_page=True, html="<html>로그인</html>")
    assert outcome is LoginOutcome.UNKNOWN
