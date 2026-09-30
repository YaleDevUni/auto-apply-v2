"""fill 시스템 프롬프트 (§A6) — 규칙·가이드 자리·지시·프로필 요약이 실리는지."""

import re
from datetime import UTC, datetime

from auto_apply.ai.fill_prompt import FillResume, build_fill_system_prompt, profile_summary
from auto_apply.contracts.fill_log import (
    SOURCE_KEY_PATTERN,
    FieldLabel,
    FillAction,
    FillEntry,
    FillLog,
    FillSource,
    FillSourceKind,
)
from auto_apply.contracts.knowledge import Answer
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
    assert _prompt().count("(없음)") == 3  # 가이드 둘·답변 KB — 비어 있으면 자리만


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


def test_answer_kb_lines_carry_the_source_key():
    kb = Answer(
        id="ans_1", user_id="local", question_key="희망 연봉", answer="5000",
        updated_at=datetime(2026, 10, 1, tzinfo=UTC),
    )  # fmt: skip
    prompt = _prompt(answers=[kb])
    assert "[답변 KB]\n- ans_1: 희망 연봉 → 5000" in prompt
    assert "ask_user" in prompt and "{kind: answer_kb, key: 그 줄 앞의 id}" in prompt
    assert "[이어서" not in prompt


def _log() -> FillLog:
    field = FieldLabel(role="textbox", name="이름", url=URL)
    name = FillEntry(
        seq=1, action=FillAction.FILL, ref="e1", field=field, value="홍길동",
        source=FillSource(kind=FillSourceKind.PROFILE, key="name"),
    )  # fmt: skip
    hidden = FillEntry(
        seq=2, step=2, action=FillAction.FILL, ref="e2", withheld=True,
        field=field.model_copy(update={"name": "장애"}),
        source=FillSource(kind=FillSourceKind.USER, key="h1"),
    )  # fmt: skip
    return FillLog(entries=(name, hidden))


def test_resume_section_lists_previous_entries_and_the_answer():
    kb = FillSource(kind=FillSourceKind.ANSWER_KB, key="ans_1")
    resume = FillResume(fill_log=_log(), question="희망 연봉", source=kb, value="5000")
    prompt = _prompt(resume=resume)
    assert '- [단계 1] 이름(textbox) fill ← "홍길동" (source {kind: profile, key: name})' in prompt
    assert (
        "- [단계 2] 장애(textbox) fill ← (값 기록 안 됨) (source {kind: user, key: h1})" in prompt
    )
    assert '받은 답: "희망 연봉" → 5000 (source {kind: answer_kb, key: ans_1})' in prompt


def test_resume_with_a_hidden_answer_shows_only_its_handle():
    user = FillSource(kind=FillSourceKind.USER, key="h9")
    held = _prompt(resume=FillResume(fill_log=FillLog(), question="장애", source=user, value=None))
    assert "앱이 채운다" in held and "{kind: user, key: h9}" in held
    lost = FillResume(fill_log=FillLog(), question="장애", source=user, value=None, held=False)
    assert "ask_user 로 다시 묻는다" in _prompt(resume=lost)
