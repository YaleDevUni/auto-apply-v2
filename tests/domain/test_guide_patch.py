"""domain/guide_patch.py — 순수 함수, LLM 이 낸 {old,new} 치환 쌍 적용 (REVISE/general)."""

import pytest

from auto_apply.domain.errors import GuidePatchAmbiguous, GuidePatchNotFound
from auto_apply.domain.guide_patch import apply_patch, apply_patches


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


def test_apply_patches_applies_all_in_order():
    """한 REVISE(general) 피드백에 서로 다른 지시가 여러 개 섞여 있으면 모두 반영돼야 한다

    (메모리 resume-revise-feedback-design — old/new 단일 쌍만 표현할 때 다지시 피드백 중
    일부가 조용히 누락되는 문제가 있었다).
    """
    text = "항상 존댓말로 쓴다.\n불릿은 한 줄로 쓴다."
    patched = apply_patches(
        text,
        [
            ("불릿은 한 줄로 쓴다.", "불릿은 두 줄까지 허용한다."),
            ("", "프로젝트는 3~4개만 싣는다."),
        ],
    )
    assert patched == (
        "항상 존댓말로 쓴다.\n불릿은 두 줄까지 허용한다.\n\n프로젝트는 3~4개만 싣는다."
    )


def test_apply_patches_stops_at_first_failure_leaving_earlier_patches_applied():
    """중간 patch 가 실패하면 그 앞까지만 적용된 상태로 예외가 전파된다 — 원자적이지 않다

    (부분 적용을 허용해도 되는 이유는 실패 예외 둘 다 재시도 없이 사람에게 넘어가기
    때문이다, domain/guide_patch.py::apply_patches 참고).
    """
    text = "항상 존댓말로 쓴다."
    with pytest.raises(GuidePatchNotFound):
        apply_patches(
            text,
            [
                ("항상 존댓말로 쓴다.", "가끔 존댓말로 쓴다."),
                ("없는 문장", "새 문장"),
            ],
        )
