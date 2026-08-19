"""canonical_key 는 중복지원 방어선이다 — 정규화가 느슨해지거나 너무 세지면

각각 '같은 자리에 두 번 지원' / '한 자리를 통째로 놓침'으로 이어진다.
"""

from auto_apply.contracts.job import JobPosting
from auto_apply.domain.job_identity import (
    canonical_key,
    content_hash,
    headline_text,
    normalize_company,
    normalize_title,
    searchable_text,
)


def _job(**kw) -> JobPosting:
    base = dict(
        platform="wanted",
        platform_job_id="1",
        url="https://wanted.co.kr/wd/1",
        company="라스트스프링",
        title="백엔드 개발자",
    )
    base.update(kw)
    return JobPosting(**base)


def test_normalize_company_strips_legal_form_and_latin_alias():
    assert normalize_company("(주) 라스트스프링(LASTSPRING)") == normalize_company("라스트스프링")


def test_normalize_title_strips_boilerplate_but_keeps_korean_qualifier():
    a = normalize_title("[2026 상반기] 백엔드 개발자 신입 채용")
    b = normalize_title("백엔드 개발자")
    assert a == b
    # 한글 괄호 내용은 직무 구분일 수 있으므로 지우지 않는다
    assert normalize_title("개발자(백엔드)") != normalize_title("개발자(프론트엔드)")


def test_canonical_key_merges_platform_notation_variants():
    a = canonical_key("(주) 라스트스프링(LASTSPRING)", "[2026 상반기] 백엔드 개발자 신입 채용")
    b = canonical_key("라스트스프링", "백엔드 개발자")
    assert a == b


def test_canonical_key_distinguishes_real_role_split():
    web = canonical_key("이노션", "웹 서비스 기획")
    app = canonical_key("이노션", "앱 서비스 기획")
    assert web != app


def test_canonical_key_empty_when_both_blank():
    assert canonical_key("", "") == ""


def test_content_hash_changes_when_body_changes():
    a = content_hash(_job(description="1"))
    b = content_hash(_job(description="2"))
    assert a != b


def test_content_hash_stable_for_same_facts():
    assert content_hash(_job(description="x")) == content_hash(_job(description="x"))


def test_searchable_text_joins_all_free_text_fields():
    text = searchable_text(_job(location="서울", description="파이썬 백엔드"))
    assert "서울" in text
    assert "파이썬" in text


def test_headline_text_excludes_description_noise():
    job = _job(category="개발", description="채용 절차 안내: 서류 → 면접")
    head = headline_text(job)
    assert "개발" in head
    assert "채용" not in head  # 본문 잡음은 headline에 안 들어간다
