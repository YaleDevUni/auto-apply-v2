"""telegram/bridge.py 의 파싱/거절 분기 — Temporal 없이도 검증되는 부분만 오프라인으로.

nonce 소비·signal 전달까지 포함한 end-to-end 는 tests/api/test_telegram_webhook_api.py 가
실제 워크플로우로 검증한다 (웹훅 라우트가 이 모듈을 그대로 호출하므로 중복이 아니다).
"""

import pytest

from auto_apply.telegram.bridge import _GUIDE_REVISE_TAG_RE as GUIDE_REVISE_TAG_RE
from auto_apply.telegram.bridge import _REVISE_TAG_RE as REVISE_TAG_RE
from auto_apply.telegram.bridge import MalformedCallback, _parse, _parse_scope_choice


def test_parse_splits_action_application_id_nonce():
    assert _parse("a:app_1:nonce_1") == ("a", "app_1", "nonce_1")
    assert _parse("r:app_1:nonce_1") == ("r", "app_1", "nonce_1")


def test_parse_accepts_revise_start_and_guide_patch_actions():
    assert _parse("v:app_1:nonce_1") == ("v", "app_1", "nonce_1")
    assert _parse("ga:app_1:nonce_1") == ("ga", "app_1", "nonce_1")
    assert _parse("gr:app_1:nonce_1") == ("gr", "app_1", "nonce_1")
    assert _parse("gv:app_1:nonce_1") == ("gv", "app_1", "nonce_1")


def test_parse_accepts_revise_cancel_action():
    assert _parse("vc:app_1:nonce_1") == ("vc", "app_1", "nonce_1")


def test_parse_accepts_guide_patch_cancel_action():
    assert _parse("gc:app_1:nonce_1") == ("gc", "app_1", "nonce_1")


def test_parse_accepts_repair_promotion_actions():
    """pa/pr(recipe 승격 승인/보류, §2.4) — application_id 자리는 "{platform}-{form_hash}"다."""
    assert _parse("pa:fixture-h1:nonce_1") == ("pa", "fixture-h1", "nonce_1")
    assert _parse("pr:fixture-h1:nonce_1") == ("pr", "fixture-h1", "nonce_1")


def test_parse_rejects_unknown_action():
    with pytest.raises(MalformedCallback):
        _parse("x:app_1:nonce_1")


def test_parse_rejects_missing_parts():
    with pytest.raises(MalformedCallback):
        _parse("a:app_1")


def test_parse_allows_colon_inside_nonce_but_not_application_id():
    """maxsplit=2 이므로 세 번째 조각(nonce) 안의 콜론은 그대로 보존된다."""
    assert _parse("a:app_1:nonce:with:colons") == ("a", "app_1", "nonce:with:colons")


def test_parse_scope_choice_splits_application_id_scope_nonce():
    assert _parse_scope_choice("vs:app_1:specific:nonce_1") == ("app_1", "specific", "nonce_1")
    assert _parse_scope_choice("vs:app_1:general:nonce:with:colons") == (
        "app_1",
        "general",
        "nonce:with:colons",
    )


def test_parse_scope_choice_rejects_unknown_scope():
    with pytest.raises(MalformedCallback):
        _parse_scope_choice("vs:app_1:unknown:nonce_1")


def test_parse_scope_choice_rejects_wrong_prefix():
    with pytest.raises(MalformedCallback):
        _parse_scope_choice("v:app_1:specific:nonce_1")


def test_revise_tag_regex_extracts_application_id_nonce_scope():
    text = "✏️ 이번 지원에만 반영할 피드백을 입력해 답장하세요.\n[revise:app_1:nonce_1:specific]"
    match = REVISE_TAG_RE.search(text)
    assert match is not None
    assert match.groups() == ("app_1", "nonce_1", "specific")


def test_revise_tag_regex_does_not_match_plain_text():
    assert REVISE_TAG_RE.search("그냥 일반 대화 메시지입니다") is None


def test_guide_revise_tag_regex_extracts_application_id_nonce():
    text = "💬 이 가이드 patch 제안에 대한 코멘트를 입력해 답장하세요.\n[guiderevise:app_1:nonce_1]"
    match = GUIDE_REVISE_TAG_RE.search(text)
    assert match is not None
    assert match.groups() == ("app_1", "nonce_1")


def test_guide_revise_tag_regex_does_not_match_plain_text():
    assert GUIDE_REVISE_TAG_RE.search("그냥 일반 대화 메시지입니다") is None
