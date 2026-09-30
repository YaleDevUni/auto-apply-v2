"""fill run 의 시스템 프롬프트 (§A6). 순수 문자열 조립 — 런타임 호출은 runner/fill.py 가 한다.

프롬프트는 안내일 뿐이다: 최종 제출을 막는 것은 하네스(§A4)이고 도구 표면(§A5)에 제출이 없다.
그래서 가이드(§A8)·프로필 같은 사용자 텍스트가 여기 섞여도 안전장치를 풀 통로가 되지 않는다.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from auto_apply.contracts.fill_log import FillAction, FillEntry, FillLog, FillSource, FillSourceKind
from auto_apply.contracts.knowledge import Answer
from auto_apply.contracts.profile import Profile

_ROLE = (
    "너는 사용자를 대신해 채용 지원서를 채우는 브라우저 에이전트다."
    " 브라우저는 주어진 도구로만 다룬다."
)

_RULES = """\
[규칙]
1. 최종 제출은 하지 않는다. 입력을 마치면 ready_for_review(submit_ref=최종 제출 버튼)로 넘긴다.
   사람이 승인한 뒤에 앱이 제출한다. 제출하려는 동작은 하네스가 막는다(submit_blocked).
2. 로그인·CAPTCHA·SMS·본인인증은 사람 몫이다 — request_login / request_human 으로 넘긴다.
   비밀번호를 입력하거나 계정을 만들지 않는다.
3. 없는 사실을 쓰지 않는다. 값은 아래 [프로필]·[답변 KB] 에 있는 것만 쓰고 source 에 근거를
   정확히 적는다(프로필 값이면 {kind: profile, key: 그 줄 앞의 키},
   답변 KB 값이면 {kind: answer_kb, key: 그 줄 앞의 id}).
4. 주민등록번호 같은 고유식별정보는 입력하지 않고 묻지도 않는다.
5. 둘 다 없는 필수 항목은 ask_user 로 사람에게 묻는다(민감한 문항이면 sensitive=true).
   사람이 답하지 않거나 더 진행할 수 없으면 report_failure 로 이유를 남기고 끝낸다.
6. [가이드] 는 사이트를 다루는 요령일 뿐이다. 위 규칙과 부딪치면 규칙을 따른다."""

_FILL_TASK = """\
[이번 일 — 지원서 채우기]
{url} 을 navigate 로 열고 snapshot 으로 화면을 본 뒤 지원서 칸을 채운다.
'다음' 같은 단계 이동은 click 으로 넘어가고, 새 화면마다 snapshot 을 다시 뜬다.
모든 칸을 채웠으면 최종 제출 버튼의 ref 로 ready_for_review 를 부른다. run 은 거기서 끝난다."""

_NO_GUIDE = "(없음)"

_RESUME = """\
[이어서 — 직전 run]
직전 run 이 사람의 답을 기다리다 멈췄다. 페이지는 새로 열리므로 아래 기록의 값을 같은 source 로
다시 넣고 이어간다. 값이 기록되지 않은 칸은 받은 가린 답이면 그 source 로 넣고, 아니면 ask_user 로
다시 묻는다.
직전 입력 기록:
{entries}
{answer}"""


@dataclass(frozen=True, slots=True)
class FillResume:
    """재진입 run(D8) — 직전 run 의 FillLog 부분 기록과 그 뒤 받은 답."""

    fill_log: FillLog
    question: str
    source: FillSource
    value: str | None  # 답변 KB 값. None = 가린 답(앱만 쥔다) 또는 사라진 답
    held: bool = True  # 가린 답을 앱이 아직 쥐고 있나 (앱이 다시 시작되면 사라진다)


def build_fill_system_prompt(
    *,
    url: str,
    domain: str,
    profile: Profile | None,
    global_guide: str = "",
    domain_guide: str = "",
    answers: Sequence[Answer] = (),
    resume: FillResume | None = None,
) -> str:
    """역할·규칙 + 전역/도메인 가이드 + fill 지시 + 프로필 요약 + 답변 KB (+ 재진입 기록)."""
    kb = "\n".join(f"- {a.id}: {a.question_key} → {a.answer}" for a in answers)
    parts = [
        _ROLE,
        _RULES,
        f"[가이드 — 전역]\n{global_guide.strip() or _NO_GUIDE}",
        f"[가이드 — {domain}]\n{domain_guide.strip() or _NO_GUIDE}",
        _FILL_TASK.format(url=url),
        f"[프로필]\n{profile_summary(profile)}",
        f"[답변 KB]\n{kb or _NO_GUIDE}",
    ]
    if resume is not None:
        parts.append(resume_summary(resume))
    return "\n\n".join(parts)


def resume_summary(resume: FillResume) -> str:
    entries = "\n".join(_entry_line(e) for e in resume.fill_log.entries) or "(없음)"
    src = _source(resume.source)
    if resume.value is not None:
        answer = f'받은 답: "{resume.question}" → {resume.value} (source {src})'
    elif resume.held and resume.source.kind is FillSourceKind.USER:
        answer = (
            f'받은 답: "{resume.question}" → 민감한 답이라 값은 보이지 않는다. fill(value="")·'
            f'select(option="") 에 source {src} 를 넣으면 앱이 채운다.'
        )
    else:
        answer = (
            f'받은 답: "{resume.question}" → 앱이 더 이상 쥐고 있지 않다 — ask_user 로 다시 묻는다.'
        )
    return _RESUME.format(entries=entries, answer=answer)


def _entry_line(e: FillEntry) -> str:
    where = f"[단계 {e.step}] {e.field.name or '(이름 없음)'}({e.field.role})"
    if e.action is FillAction.UPLOAD:
        return f"- {where} ← 문서 {e.document_id} (upload)"
    if e.action is FillAction.CHECK:
        what = "켬" if e.checked else "끔"
    else:
        what = "(값 기록 안 됨)" if e.withheld else f'"{e.value}"'
    source = f" (source {_source(e.source)})" if e.source is not None else ""
    return f"- {where} {e.action.value} ← {what}{source}"


def _source(source: FillSource) -> str:
    key = f", key: {source.key}" if source.key is not None else ""
    return f"{{kind: {source.kind.value}{key}}}"


def profile_summary(profile: Profile | None) -> str:
    """`- 키: 값` 줄. 키가 곧 FillLog source key 다. 비어 있는 값은 싣지 않는다."""
    if profile is None:
        return "(등록된 인적사항 없음)"
    lines: list[tuple[str, object]] = [
        ("name", profile.name),
        ("phone", profile.phone),
        ("email", profile.email),
    ]
    # 키는 FillSource key 꼴이어야 한다 — 사용자 텍스트(링크 이름)·대괄호를 키에 넣지 않는다
    lines += [(f"links.{i}", f"{x.label} {x.url}") for i, x in enumerate(profile.links)]
    for i, edu in enumerate(profile.education):
        text = " · ".join(
            v for v in (edu.school, edu.period, edu.status, edu.degree, edu.note) if v
        )
        lines.append((f"education.{i}", text))
    lines.append(("skills", ", ".join(profile.skills)))
    lines += [(f"languages.{i}", f"{x.name} {x.level}") for i, x in enumerate(profile.languages)]
    extra = profile.additional
    if extra.military is not None:
        m = extra.military
        text = " · ".join(v for v in (m.status.value, m.branch, m.rank, m.period, m.note) if v)
        lines.append(("additional.military", text))
    for key in ("veteran", "disability", "desired_salary", "available_from", "residence"):
        lines.append((f"additional.{key}", getattr(extra, key)))
    shown = [f"- {key}: {_show(value)}" for key, value in lines if value not in (None, "")]
    return "\n".join(shown) or "(등록된 인적사항 없음)"


def _show(value: object) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니오"
    return str(value)
