"""완료 어휘 판정(§A4 L5)과 어휘 데이터의 형태 검사."""

import unicodedata

import pytest

from auto_apply.domain import submit_vocabulary as vocab
from auto_apply.domain.submit_classifier import detect_completion


@pytest.mark.parametrize(
    "text",
    [
        "지원이 완료되었습니다.",
        "지원이 정상적으로 완료되었습니다",
        "지원 완료되었습니다",
        "입사지원이 완료됐습니다",
        "지원서가 제출되었습니다",
        "지원서가 성공적으로 접수되었습니다",
        "접수가 완료되었습니다",
        "지원해 주셔서 감사합니다",
        "지원이\n완료 되었습니다",
        "Application received",
        "Your application has been submitted.",
        "Your application was successfully received!",
        "Thank you for applying!",
        "Thanks for your application",
        "We've received your application",
        "We\u2019ve received your application",
        "APPLICATION  SUBMITTED",
        "\uff21pplication received",
        "You have successfully applied",
        "Submission received",
        "Application\u200b received",  # zero-width 로 쪼갠 문구
        "Thank you for ap\u00adplying",  # soft hyphen
    ],
)
def test_completion_text_detected(text):
    assert detect_completion(text=text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "지원이 완료되면 이메일로 안내드립니다",  # 폼 안내문
        "지원 완료",  # 제출 버튼 라벨
        "최종 제출",
        "제출하기",
        "이력서가 임시저장되었습니다",
        "파일이 업로드되었습니다",
        "지원자 정보가 저장되었습니다",
        "Submit application",
        "Apply now",
        "Application form",
        "Thank you for your interest in this role",
        "Review your application before submitting",
        "",
    ],
)
def test_completion_text_not_detected(text):
    assert detect_completion(text=text) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.example.com/apply/thank-you",
        "https://jobs.example.com/jobs/1/thanks",
        "/application/submitted",
        "https://example.com/apply/confirmation?id=3",
        "https://example.com/apply?step=completed",
        "https://example.com/%74hanks",
    ],
)
def test_completion_url_detected(url):
    assert detect_completion(url=url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://thankyou.example.com/jobs/1/apply",  # 호스트는 보지 않는다
        "https://example.com/jobs/customer-success-manager",
        "https://example.com/jobs/applied-ai-engineer",
        "https://example.com/profile/complete",
        "https://example.com/jobs/1/apply",
        "",
    ],
)
def test_completion_url_not_detected(url):
    assert detect_completion(url=url) is None


def test_completion_reports_its_evidence():
    assert detect_completion(url="/x/thanks") == "url:thanks"
    assert detect_completion(text="Application received") == "text:application received"


def _compact(word: str) -> str:
    folded = unicodedata.normalize("NFKC", word).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


@pytest.mark.parametrize(
    "words",
    [
        vocab.RISKY_KO,
        vocab.RISKY_EN_STEMS,
        vocab.RISKY_EN_TOKENS,
        vocab.DIALOG_AFFIRMATIVE,
        vocab.SAFE_KO,
        vocab.SAFE_EN_TOKENS,
        vocab.COMPLETION_URL_TOKENS,
    ],
)
def test_vocabulary_is_stored_normalized(words):
    # 정규화되지 않은 항목은 압축형·토큰과 절대 같아질 수 없어 조용히 죽은 어휘가 된다.
    assert all(w == _compact(w) and w for w in words)


def test_short_english_stems_live_in_token_list():
    # 짧은 말을 부분 문자열로 찾으면 "bookmark" 안의 "ok" 처럼 엉뚱한 곳에 걸린다.
    assert all(len(stem) >= 4 for stem in vocab.RISKY_EN_STEMS)


def test_no_safe_word_is_shadowed_by_a_risky_word():
    # 위험 어휘를 품은 안전 어휘는 절대 Safe 로 판정될 수 없다 — 목록 편집 실수를 잡는다.
    risky = (*vocab.RISKY_KO, *vocab.RISKY_EN_STEMS)
    for safe in (*vocab.SAFE_KO, *vocab.SAFE_EN_TOKENS):
        assert not any(r in safe for r in risky), safe
