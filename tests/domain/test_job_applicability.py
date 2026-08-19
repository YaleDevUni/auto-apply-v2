"""evaluate_applicability() 회귀 테스트 — 구 프로젝트 실측 사례 기준."""

from datetime import date, timedelta

from auto_apply.contracts.job import JobPosting, ScreeningVerdict
from auto_apply.contracts.matching_config import ApplicabilityRules, EssayConfig
from auto_apply.domain.enums import BlockerCode
from auto_apply.domain.job_applicability import evaluate_applicability


def _job(**kw) -> JobPosting:
    base = dict(
        platform="wanted",
        platform_job_id="1",
        url="https://wanted.co.kr/wd/1",
        company="회사",
        title="백엔드 개발자",
        description="충분히 긴 본문입니다. " * 20,
    )
    base.update(kw)
    return JobPosting(**base)


def _screening(**kw) -> ScreeningVerdict:
    base = dict(verdict="pass", fit_score=80)
    base.update(kw)
    return ScreeningVerdict(**base)


def _cfg(**kw) -> ApplicabilityRules:
    base = dict(min_fit_score=60, min_description_chars=50, max_required_gaps=2, min_days_left=0)
    base.update(kw)
    return ApplicabilityRules(**base)


def _codes(v):
    return {b.code for b in v.blockers}


def test_fully_passing_case_is_actionable():
    v = evaluate_applicability(_job(), _screening(), _cfg(), recipe_exists=True, session_ok=True)
    assert v.actionable
    assert v.channel == "platform_form"


def test_score_below_bar_blocks():
    v = evaluate_applicability(
        _job(), _screening(fit_score=10), _cfg(), recipe_exists=True, session_ok=True
    )
    assert not v.actionable
    assert BlockerCode.SCORE_BELOW_BAR in _codes(v)


def test_external_ats_uses_employment_page_url_and_blocks():
    job = _job(
        url="https://wanted.co.kr/wd/1",
        raw={"employment_page_url": "https://boards.greenhouse.io/acme/jobs/1"},
    )
    v = evaluate_applicability(job, _screening(), _cfg(), recipe_exists=True, session_ok=True)
    assert v.channel == "external_ats"
    assert v.apply_url == "https://boards.greenhouse.io/acme/jobs/1"
    assert BlockerCode.EXTERNAL_ATS in _codes(v)
    assert v.confidence <= 0.6


def test_image_only_channel_is_determined_by_image_path_not_url():
    """image_url만 있고 image_path가 없으면 (표지 썸네일) image_only가 아니다."""
    job = _job(image_url="https://x/thumb.png")
    v = evaluate_applicability(job, _screening(), _cfg(), recipe_exists=True, session_ok=True)
    assert v.channel == "platform_form"

    job2 = _job(image_url="https://x/thumb.png", image_path="job-images/jasoseol/1.png")
    v2 = evaluate_applicability(job2, _screening(), _cfg(), recipe_exists=True, session_ok=True)
    assert v2.channel == "image_only"
    assert BlockerCode.IMAGE_ONLY in _codes(v2)


def test_session_unknown_lowers_confidence_without_blocking():
    v = evaluate_applicability(_job(), _screening(), _cfg(), recipe_exists=True, session_ok=None)
    assert BlockerCode.LOGIN_REQUIRED not in _codes(v)
    assert v.confidence < 1.0


def test_session_dead_blocks():
    v = evaluate_applicability(_job(), _screening(), _cfg(), recipe_exists=True, session_ok=False)
    assert BlockerCode.LOGIN_REQUIRED in _codes(v)


def test_missing_recipe_blocks_platform_form():
    v = evaluate_applicability(_job(), _screening(), _cfg(), recipe_exists=False, session_ok=True)
    assert BlockerCode.NO_RECIPE in _codes(v)


def test_required_gaps_over_bar_blocks():
    v = evaluate_applicability(
        _job(),
        _screening(),
        _cfg(max_required_gaps=2),
        recipe_exists=True,
        session_ok=True,
        required_gaps=3,
    )
    assert BlockerCode.REQUIREMENT_GAP in _codes(v)


def test_essay_blocks_when_autowrite_off():
    job = _job(description="자기소개서 500자 이내로 지원동기를 서술하세요. " * 5)
    v = evaluate_applicability(
        job, _screening(), _cfg(), recipe_exists=True, session_ok=True, form_has_essays=True
    )
    assert BlockerCode.ESSAY_REQUIRED in _codes(v)


def test_essay_not_blocked_when_form_has_no_essay_field():
    """원티드처럼 폼에 문항 입력란이 없으면 본문의 자소서 언급은 blocker가 아니다."""
    job = _job(description="자기소개서 500자 이내로 지원동기를 서술하세요. " * 5)
    v = evaluate_applicability(
        job, _screening(), _cfg(), recipe_exists=True, session_ok=True, form_has_essays=False
    )
    assert BlockerCode.ESSAY_REQUIRED not in _codes(v)
    assert v.requires.get("essay_in_document") is True


def test_essay_autowrite_over_limit_blocks():
    job = _job(description=" ".join(f"{i}. 500자 이내로 작성하세요." for i in range(1, 5)))
    cfg = _cfg(essays=EssayConfig(autowrite=True, max_autowrite=3))
    v = evaluate_applicability(job, _screening(), cfg, recipe_exists=True, session_ok=True)
    assert BlockerCode.ESSAY_TOO_MANY in _codes(v)


def test_missing_document_blocks():
    job = _job(description="졸업증명서 제출 필수. " + "충분히 긴 본문입니다. " * 20)
    v = evaluate_applicability(job, _screening(), _cfg(), recipe_exists=True, session_ok=True)
    assert BlockerCode.DOC_MISSING in _codes(v)


def test_available_document_does_not_block():
    job = _job(description="졸업증명서 제출 필수. " + "충분히 긴 본문입니다. " * 20)
    cfg = _cfg(available_documents=["졸업증명서"])
    v = evaluate_applicability(job, _screening(), cfg, recipe_exists=True, session_ok=True)
    assert BlockerCode.DOC_MISSING not in _codes(v)


def test_short_description_blocks_and_lowers_confidence():
    job = _job(description="짧음")
    v = evaluate_applicability(job, _screening(), _cfg(), recipe_exists=True, session_ok=True)
    assert BlockerCode.NO_DETAIL in _codes(v)
    assert v.confidence <= 0.5


def test_deadline_passed_blocks():
    v = evaluate_applicability(
        _job(deadline="2000-01-01"), _screening(), _cfg(), recipe_exists=True, session_ok=True
    )
    assert BlockerCode.CLOSED in _codes(v)


def test_closing_too_soon_blocks():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    v = evaluate_applicability(
        _job(deadline=tomorrow),
        _screening(),
        _cfg(min_days_left=3),
        recipe_exists=True,
        session_ok=True,
    )
    assert BlockerCode.CLOSING_TOO_SOON in _codes(v)
