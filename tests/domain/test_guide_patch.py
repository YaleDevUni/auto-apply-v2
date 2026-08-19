"""domain/guide_patch.py — 순수 함수, LLM 이 낸 {old,new} 치환 쌍 적용 (REVISE/general)."""

import pytest

from auto_apply.domain.errors import GuidePatchAmbiguous, GuidePatchNotFound
from auto_apply.domain.guide_patch import apply_patch


def test_replaces_the_single_occurrence():
    text = "항상 존댓말로 쓴다.\n불릿은 한 줄로 쓴다."
    patched = apply_patch(text, "불릿은 한 줄로 쓴다.", "불릿은 두 줄까지 허용한다.")
    assert patched == "항상 존댓말로 쓴다.\n불릿은 두 줄까지 허용한다."


def test_empty_old_appends_to_existing_text():
    patched = apply_patch("항상 존댓말로 쓴다.", "", "숫자는 아라비아 숫자로 쓴다.")
    assert patched == "항상 존댓말로 쓴다.\n\n숫자는 아라비아 숫자로 쓴다."


def test_empty_old_on_empty_guide_just_sets_the_text():
    assert apply_patch("", "", "첫 규칙") == "첫 규칙"


def test_missing_old_raises_not_found():
    with pytest.raises(GuidePatchNotFound):
        apply_patch("항상 존댓말로 쓴다.", "없는 문장", "새 문장")


def test_ambiguous_old_raises_ambiguous():
    text = "짧게 쓴다. 다른 규칙. 짧게 쓴다."
    with pytest.raises(GuidePatchAmbiguous):
        apply_patch(text, "짧게 쓴다.", "간결하게 쓴다.")
