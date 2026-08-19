"""telegram/bridge.py 의 파싱/거절 분기 — Temporal 없이도 검증되는 부분만 오프라인으로.

nonce 소비·signal 전달까지 포함한 end-to-end 는 tests/api/test_telegram_webhook_api.py 가
실제 워크플로우로 검증한다 (웹훅 라우트가 이 모듈을 그대로 호출하므로 중복이 아니다).
"""

import pytest

from auto_apply.telegram.bridge import MalformedCallback, _parse


def test_parse_splits_action_application_id_nonce():
    assert _parse("a:app_1:nonce_1") == ("a", "app_1", "nonce_1")
    assert _parse("r:app_1:nonce_1") == ("r", "app_1", "nonce_1")


def test_parse_rejects_unknown_action():
    with pytest.raises(MalformedCallback):
        _parse("x:app_1:nonce_1")


def test_parse_rejects_missing_parts():
    with pytest.raises(MalformedCallback):
        _parse("a:app_1")


def test_parse_allows_colon_inside_nonce_but_not_application_id():
    """maxsplit=2 이므로 세 번째 조각(nonce) 안의 콜론은 그대로 보존된다."""
    assert _parse("a:app_1:nonce:with:colons") == ("a", "app_1", "nonce:with:colons")
