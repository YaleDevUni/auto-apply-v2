"""AssembledResume → HTML 문자열. weasyprint 를 몰라도 되는 순수 함수라 인프라 없이 테스트한다.

원티드 PDF 내보내기 포맷을 참고했다 — 이름/연락처 헤더, 한줄 요약, 상단 하이라이트, 경력(회사
헤더 + 하위 블록별 불릿·기술스택), 개인 프로젝트, 학력, AI 활용 경험, 스킬 태그, 언어 순서
(다른 플랫폼은 포맷이 다를 수 있다 — 지금은 이 한 가지만 구현한다).
"""

from html import escape as _e

from auto_apply.contracts.profile import EducationEntry
from auto_apply.contracts.resume_content import AssembledResume, ResumeBlockView, ResumeBulletView

_CSS = """
@page { size: A4; margin: 16mm 18mm; }
* { box-sizing: border-box; }
body {
  font-family: "Apple SD Gothic Neo", "AppleGothic", "Noto Sans KR", "Malgun Gothic", sans-serif;
  color: #1a1a1a;
  font-size: 10pt;
  line-height: 1.55;
}
h1 { font-size: 20pt; margin: 0 0 3mm; }
.contact { color: #555; font-size: 9pt; margin-bottom: 5mm; }
.contact span + span::before { content: "  ·  "; }
.tagline { font-weight: 600; font-size: 10.5pt; margin: 0 0 3mm; }
ul.highlights { margin: 0 0 6mm; padding-left: 4.5mm; }
ul.highlights li { margin-bottom: 1mm; }
section { margin-bottom: 6mm; }
h2 {
  font-size: 12pt; margin: 0 0 3mm; padding-bottom: 1.5mm;
  border-bottom: 1.2pt solid #1a1a1a;
}
.entity { margin-bottom: 4mm; }
.entity-head, .block-head, .edu-head {
  display: flex; justify-content: space-between; align-items: baseline;
}
.entity-head .company { font-weight: 700; font-size: 10.5pt; }
.block { margin: 2.5mm 0 0 3mm; }
.block-head .block-title { font-weight: 600; }
.block-head .block-link { font-size: 8.5pt; margin-left: 2mm; color: #1a5fb4; }
.period { color: #666; font-size: 9pt; white-space: nowrap; }
ul.bullets { margin: 1mm 0 1mm; padding-left: 4.5mm; }
ul.bullets li { margin-bottom: 0.8mm; }
.tech { color: #555; font-size: 8.5pt; }
.edu-entry { margin-bottom: 3mm; }
.edu-head .school { font-weight: 700; }
.edu-degree { font-size: 9.5pt; }
.edu-note { color: #666; font-size: 8.5pt; }
/* .chips 는 flex 를 안 쓴다 — weasyprint 가 flex 아이템 + border-radius 조합에서 border 를
   두 겹으로 그리는 렌더링 버그가 있다(실측, 69.0). inline-block + margin 으로 같은 줄바꿈
   레이아웃을 만들면 버그를 피해간다. */
.chip {
  display: inline-block; border: 1pt solid #ccc; border-radius: 3mm; padding: 1mm 3mm;
  font-size: 9pt; margin: 0 2mm 2mm 0;
}
.languages div + div { margin-top: 1mm; }
"""


def _bullets(items: list[ResumeBulletView], *, cls: str = "bullets") -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{_e(b.text)}</li>" for b in items)
    return f"<ul class='{cls}'>{lis}</ul>"


def _tech(tech_stack: list[str]) -> str:
    if not tech_stack:
        return ""
    return f"<div class='tech'>사용기술: {_e(', '.join(tech_stack))}</div>"


def _link_label(url: str) -> str:
    """블록 제목 옆에 붙는 링크 표시 텍스트 — href는 원문 그대로 두고, 화면엔 스킴 없는

    짧은 형태만 보여준다(경력/프로젝트 헤더 한 줄이 너무 길어지지 않게).
    """
    return url.removeprefix("https://").removeprefix("http://")


def _block(block: ResumeBlockView) -> str:
    period = f"<span class='period'>{_e(block.period)}</span>" if block.period else ""
    title = f"<span class='block-title'>[{_e(block.title)}]</span>"
    link = (
        f"<a class='block-link' href='{_e(block.url)}'>{_e(_link_label(block.url))}</a>"
        if block.url and block.url.startswith(("http://", "https://"))
        else ""
    )
    return (
        "<div class='block'>"
        f"<div class='block-head'><span>{title}{link}</span>{period}</div>"
        f"{_bullets(block.bullets)}"
        f"{_tech(block.tech_stack)}"
        "</div>"
    )


def _edu_entry(edu: EducationEntry) -> str:
    period = _e(edu.period) + (f" · {_e(edu.status)}" if edu.status else "")
    degree = f"<div class='edu-degree'>{_e(edu.degree)}</div>" if edu.degree else ""
    note = f"<div class='edu-note'>{_e(edu.note)}</div>" if edu.note else ""
    return (
        "<div class='edu-entry'>"
        "<div class='edu-head'>"
        f"<span class='school'>{_e(edu.school)}</span>"
        f"<span class='period'>{period}</span>"
        "</div>"
        f"{degree}{note}"
        "</div>"
    )


def render_resume_html(resume: AssembledResume) -> str:
    contact = "".join(f"<span>{_e(v)}</span>" for v in (resume.phone, resume.email) if v)

    career_html = "".join(
        "<div class='entity'>"
        "<div class='entity-head'>"
        f"<span class='company'>{_e(entry.company)}</span>"
        + (f"<span class='period'>{_e(entry.period)}</span>" if entry.period else "")
        + "</div>"
        + "".join(_block(b) for b in entry.blocks)
        + "</div>"
        for entry in resume.career
    )

    projects_html = "".join(_block(b) for b in resume.projects)

    ai_usage_html = (
        f"<section class='ai-usage'><h2>AI 활용 경험</h2>{_bullets(resume.ai_usage)}</section>"
        if resume.ai_usage
        else ""
    )

    education_html = "".join(_edu_entry(edu) for edu in resume.education)

    skills_html = "".join(f"<span class='chip'>{_e(s)}</span>" for s in resume.skills)

    languages_html = "".join(
        f"<div>{_e(lang.name)} · {_e(lang.level)}</div>" for lang in resume.languages
    )

    sections = [
        f"<section class='career'><h2>경력</h2>{career_html}</section>" if resume.career else "",
        f"<section class='projects'><h2>개인 프로젝트</h2>{projects_html}</section>"
        if resume.projects
        else "",
        ai_usage_html,
        f"<section class='education'><h2>학력</h2>{education_html}</section>"
        if resume.education
        else "",
        f"<section class='skills'><h2>스킬</h2><div class='chips'>{skills_html}</div></section>"
        if resume.skills
        else "",
        f"<section class='languages'><h2>언어</h2>{languages_html}</section>"
        if resume.languages
        else "",
    ]

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_CSS}</style></head><body>"
        f"<h1>{_e(resume.name)}</h1>"
        f"<div class='contact'>{contact}</div>"
        f"<p class='tagline'>{_e(resume.summary)}</p>"
        f"{_bullets(resume.highlights, cls='highlights')}"
        f"{''.join(sections)}"
        "</body></html>"
    )
