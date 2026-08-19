"""render_resume_html — 순수 문자열 조립. weasyprint/시스템 라이브러리 없이 돈다."""

from auto_apply.adapters.pdf._template import render_resume_html
from auto_apply.contracts.profile import EducationEntry, LanguageEntry
from auto_apply.contracts.resume_content import (
    AssembledResume,
    CareerEntryView,
    ResumeBlockView,
    ResumeBulletView,
)

RESUME = AssembledResume(
    name="박예일",
    phone="+821000000000",
    email="test@example.com",
    summary="풀스택 개발자입니다",
    highlights=[ResumeBulletView(text="상단 하이라이트", fact_ids=["f-1"])],
    career=[
        CareerEntryView(
            company="Acme(백엔드 인턴)",
            period="2023.01 - 2023.12",
            blocks=[
                ResumeBlockView(
                    title="결제 API 개발",
                    period="2023.01 - 2023.06",
                    bullets=[ResumeBulletView(text="결제 API 를 설계했다", fact_ids=["f-2"])],
                    tech_stack=["FastAPI", "PostgreSQL"],
                )
            ],
        )
    ],
    projects=[
        ResumeBlockView(
            title="칸반 협업 시스템",
            period="2024.01 - 2024.02",
            bullets=[ResumeBulletView(text="실시간 칸반 도구를 구현했다", fact_ids=["f-3"])],
            tech_stack=["NestJS"],
        )
    ],
    ai_usage=[ResumeBulletView(text="LLM 파이프라인 설계 경험", fact_ids=["f-4"])],
    education=[
        EducationEntry(
            school="Example University", period="2020.01 - 2024.02", status="졸업", degree="학사"
        )
    ],
    skills=["React", "Python"],
    languages=[LanguageEntry(name="영어", level="고급")],
)


def test_render_resume_html_includes_every_section():
    html = render_resume_html(RESUME)
    assert "박예일" in html
    assert "풀스택 개발자입니다" in html
    assert "상단 하이라이트" in html
    assert "Acme(백엔드 인턴)" in html
    assert "[결제 API 개발]" in html
    assert "사용기술: FastAPI, PostgreSQL" in html
    assert "칸반 협업 시스템" in html
    assert "LLM 파이프라인 설계 경험" in html
    assert "Example University" in html
    assert "React" in html
    assert "영어" in html


def test_render_resume_html_escapes_user_content():
    resume = RESUME.model_copy(update={"summary": "<script>alert(1)</script>"})
    html = render_resume_html(resume)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_resume_html_omits_empty_optional_sections():
    minimal = AssembledResume(name="이름만", summary="요약")
    html = render_resume_html(minimal)
    assert "<h2>개인 프로젝트</h2>" not in html
    assert "<h2>AI 활용 경험</h2>" not in html
    assert "<h2>스킬</h2>" not in html
