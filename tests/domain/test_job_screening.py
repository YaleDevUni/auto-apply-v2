"""screen() 회귀 테스트. 구 프로젝트가 실측으로 잡아낸 오탐 사례를 재현한다 —

값(키워드 목록)은 최소 세트로 새로 만들되, 지켜야 하는 규칙(로직)은 그대로.
"""

from auto_apply.contracts.job import JobPosting
from auto_apply.contracts.matching_config import (
    HardcutRule,
    LocationConfig,
    MatchingConfig,
    ScoringConfig,
    TrackRule,
)
from auto_apply.domain.job_screening import screen


def _cfg(**overrides) -> MatchingConfig:
    base = dict(
        tracks={
            "dev": TrackRule(
                label="개발",
                weight=100,
                keywords=["백엔드", "python", "pm", "개발"],
                categories=["개발"],
            ),
            "biz": TrackRule(
                label="경영지원",
                weight=70,
                keywords=["영업", "채용"],
                headline_keywords=["채용"],
            ),
            "mes_erp": TrackRule(
                label="MES",
                weight=95,
                keywords=["mes"],
                overrides_hardcut=["TRADE_FIELD"],
            ),
        },
        hardcuts={
            "EXPERIENCE_REQUIRED": HardcutRule(
                label="경력 필수",
                keywords=["경력 3년"],
                unless=["신입"],
            ),
            "TRADE_FIELD": HardcutRule(
                label="현장직",
                keywords=["생산직"],
                always=["오퍼레이터"],
            ),
        },
        scoring=ScoringConfig(
            keywords_stack=["react", "python"],
            stack_bonus=4,
            stack_max=6,
            keywords_newbie=["신입"],
            newbie_bonus=18,
        ),
        location=LocationConfig(preferred=["서울"], preferred_bonus=10),
    )
    base.update(overrides)
    return MatchingConfig(**base)


def _job(**kw) -> JobPosting:
    base = dict(
        platform="wanted",
        platform_job_id="1",
        url="https://x/1",
        company="회사",
        title="백엔드 개발자",
        description="",
    )
    base.update(kw)
    return JobPosting(**base)


def test_headline_keyword_outweighs_more_body_hits():
    """제목 1개(가중 3)가 본문 잡음 다건을 이긴다 — biz의 '채용' 공짜점수 문제 재현."""
    job = _job(
        title="백엔드 개발자 채용",  # dev: 헤드라인 '백엔드'
        category=None,
        description="채용 절차 안내. 채용 후 온보딩. 채용팀 문의.",  # biz: 본문 '채용' x3
    )
    v = screen(job, _cfg())
    assert v.track == "dev"


def test_category_requires_exact_match_not_substring():
    """'사업개발'이 '개발' 카테고리로 부분일치되면 CATEGORY_WEIGHT를 공짜로 받는다 —

    구 프로젝트가 실측으로 막은 오탐. 키워드 '개발'은 한글이라 부분일치로 여전히
    잡히지만(문서화된 동작), 카테고리 보너스만큼은 정확히 일치할 때만 붙어야 한다.
    """
    substring_cat = _job(title="사업개발 담당자", category="사업개발", description="")
    exact_cat = _job(title="사업개발 담당자", category="개발", description="")

    v_substring = screen(substring_cat, _cfg())
    v_exact = screen(exact_cat, _cfg())

    assert v_substring.verdict == "pass"
    assert v_exact.verdict == "pass"
    substring_hits = v_substring.score_detail["트랙"]["근거"]
    exact_hits = v_exact.score_detail["트랙"]["근거"]
    assert not any(h.startswith("직무그룹:") for h in substring_hits)
    assert any(h.startswith("직무그룹:") for h in exact_hits)


def test_exact_category_match_wins_over_off_track():
    job = _job(title="Cloud Platform Engineer", category="개발", description="")
    v = screen(job, _cfg())
    assert v.track == "dev"
    assert "직무그룹:개발" in v.score_detail["트랙"]["근거"]


def test_hardcut_excludes_without_llm_call():
    job = _job(title="백엔드 개발자", description="경력 3년 이상")
    v = screen(job, _cfg())
    assert v.verdict == "excluded"
    assert v.exclude_code == "EXPERIENCE_REQUIRED"


def test_unless_keyword_cancels_hardcut():
    job = _job(title="백엔드 개발자", description="경력 3년 또는 신입 가능")
    v = screen(job, _cfg())
    assert v.verdict == "pass"


def test_always_keyword_survives_track_override():
    """MES 트랙이 TRADE_FIELD를 면제해도, always 신호는 못 넘는다."""
    job = _job(title="MES 생산관리 오퍼레이터", description="")
    v = screen(job, _cfg())
    assert v.verdict == "excluded"
    assert v.exclude_code == "TRADE_FIELD"


def test_track_override_without_always_signal_passes_hardcut():
    job = _job(title="MES 생산관리 담당자", description="")
    v = screen(job, _cfg())
    assert v.verdict == "pass"
    assert v.track == "mes_erp"


def test_deadline_passed_is_closed():
    job = _job(deadline="2000-01-01")
    v = screen(job, _cfg())
    assert v.exclude_code == "CLOSED"


def test_off_track_when_no_track_matches():
    job = _job(title="아무 관련 없는 제목", category=None, description="")
    v = screen(job, _cfg())
    assert v.exclude_code == "OFF_TRACK"


def test_stack_bonus_is_capped_at_stack_max():
    job = _job(title="백엔드 개발자", description="react python 스택 사용")
    v = screen(job, _cfg())
    assert v.score_detail["기술스택"]["점수"] == 6  # 4*2=8 이지만 stack_max=6


def test_score_has_no_upper_clamp():
    job = _job(
        title="백엔드 개발자 신입",
        description="react python 서울 신입",
        location="서울",
    )
    v = screen(job, _cfg())
    # base(50) + stack(6, capped) + newbie(18) + location(10) = 84
    assert v.fit_score == 84
