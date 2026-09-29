"""답변KB 질문 키 정규화 (§A7)."""

import pytest

from auto_apply.domain.errors import InvalidInput
from auto_apply.domain.question_key import normalize_question_key


@pytest.mark.parametrize(
    "raw",
    [
        "희망 연봉은?",
        "  희망   연봉은? *",
        "희망 연봉은\uff1a",  # 전각 콜론
        "희망\u200b 연봉은",
        "희망\n연봉은?!",
    ],
)
def test_question_key_folds_form_noise(raw):
    assert normalize_question_key(raw) == "희망 연봉은"


def test_question_key_casefolds_and_nfkc():
    fullwidth = "\uff37\uff4f\uff52\uff4b permit"  # 전각 "Work"
    assert normalize_question_key("Work Permit?") == normalize_question_key(fullwidth)


@pytest.mark.parametrize("raw", ["", "   ", "?*:"])
def test_question_key_rejects_empty(raw):
    with pytest.raises(InvalidInput):
        normalize_question_key(raw)
