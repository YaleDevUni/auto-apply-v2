"""fill 시스템 프롬프트 (§A6) — 규칙·가이드 자리·지시·프로필 요약이 실리는지."""

import re

from auto_apply.ai.fill_prompt import build_fill_system_prompt, profile_summary
from auto_apply.contracts.fill_log import SOURCE_KEY_PATTERN
from auto_apply.contracts.profile import (
    AdditionalInfo,
    EducationEntry,
    LanguageEntry,
    MilitaryService,
    Profile,
    ProfileLink,
)
from auto_apply.domain.enums import MilitaryStatus

URL = "https://jobs.example.com/p/1"


def _prompt(**kw) -> str:
    base = {"url": URL, "domain": "jobs.example.com", "profile": None}
    return build_fill_system_prompt(**(base | kw))


def test_rules_and_task_are_present():
    prompt = _prompt()
    assert URL in prompt and "ready_for_review" in prompt
    rules = ("최종 제출은 하지 않는다", "request_login", "비밀번호", "없는 사실", "주민등록번호")
    assert all(rule in prompt for rule in rules)
    assert "(등록된 인적사항 없음)" in prompt


def test_guides_have_their_own_slots_and_rules_come_first():
    prompt = _prompt(global_guide="표는 위에서부터", domain_guide="로그인은 우측 상단")
    assert "[가이드 — 전역]\n표는 위에서부터" in prompt
    assert "[가이드 — jobs.example.com]\n로그인은 우측 상단" in prompt
    assert prompt.index("[규칙]") < prompt.index("[가이드 — 전역]")
    assert _prompt().count("(없음)") == 2  # 비어 있으면 자리만


def test_profile_summary_keys_are_fill_source_keys():
    profile = Profile(
        user_id="local",
        name="홍길동",
        email="hong@example.com",
        links=[ProfileLink(label="GitHub", url="https://github.com/hong")],
        education=[EducationEntry(school="한국대", period="2015-2019", status="졸업")],
        skills=["Python", "FastAPI"],
        additional=AdditionalInfo(
            military=MilitaryService(status=MilitaryStatus.COMPLETED, branch="육군"),
            veteran=False,
            desired_salary="회사 내규",
        ),
    )
    lines = profile_summary(profile).splitlines()
    assert lines == [
        "- name: 홍길동",
        "- email: hong@example.com",
        "- links.0: GitHub https://github.com/hong",
        "- education.0: 한국대 · 2015-2019 · 졸업",
        "- skills: Python, FastAPI",
        "- additional.military: completed · 육군",
        "- additional.veteran: 아니오",  # False 는 "해당 없음" 이라 싣는다, None 은 모름이라 뺀다
        "- additional.desired_salary: 회사 내규",
    ]


def test_every_summary_key_is_a_valid_fill_source_key():
    # 에이전트가 프롬프트의 키를 그대로 source.key 로 쓰면 FillLog 가 받아야 한다
    profile = Profile(
        user_id="local",
        name="홍길동",
        links=[ProfileLink(label="내 포트폴리오 사이트", url="https://hong.example.com")],
        education=[
            EducationEntry(school="한국대", period="2015-2019"),
            EducationEntry(school="한국고", period="2012-2015"),
        ],
        languages=[LanguageEntry(name="영어", level="비즈니스")],
    )
    keys = [line[2:].split(": ", 1)[0] for line in profile_summary(profile).splitlines()]
    assert keys == ["name", "links.0", "education.0", "education.1", "languages.0"]
    assert all(re.fullmatch(SOURCE_KEY_PATTERN, k) for k in keys)
