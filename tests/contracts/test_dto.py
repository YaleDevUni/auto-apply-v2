"""StartApplication.application_id 상한 — 텔레그램 callback_data 64바이트 한계 회귀.

전체 근거/실측은 tests/adapters/test_telegram_notifier.py 의
test_scope_picker_callback_data_fits_telegram_limit 참고. 여기서는 DTO 가 그 상한을
실제로 거부하는지만 본다.
"""

import pytest
from pydantic import ValidationError

from auto_apply.contracts.dto import StartApplication


def _cmd(application_id: str) -> StartApplication:
    return StartApplication(application_id=application_id, user_id="u1", job_url="https://x")


def test_application_id_at_max_length_is_accepted() -> None:
    _cmd("a" * 24)


def test_application_id_over_max_length_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _cmd("a" * 25)
