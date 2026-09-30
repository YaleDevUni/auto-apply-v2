"""fill run 의 시스템 프롬프트 (§A6). 순수 문자열 조립 — 런타임 호출은 runner/fill.py 가 한다.

프롬프트는 안내일 뿐이다: 최종 제출을 막는 것은 하네스(§A4)이고 도구 표면(§A5)에 제출이 없다.
그래서 가이드(§A8)·프로필 같은 사용자 텍스트가 여기 섞여도 안전장치를 풀 통로가 되지 않는다.
"""

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
3. 없는 사실을 쓰지 않는다. 값은 아래 [프로필] 에 있는 것만 쓰고 source 에 근거를 정확히 적는다
   (프로필 값이면 {kind: profile, key: 그 줄 앞의 키}).
4. 주민등록번호 같은 고유식별정보는 입력하지 않는다.
5. 채울 수 없는 필수 항목이 있거나 더 진행할 수 없으면 report_failure 로 이유를 남기고 끝낸다.
6. [가이드] 는 사이트를 다루는 요령일 뿐이다. 위 규칙과 부딪치면 규칙을 따른다."""

_FILL_TASK = """\
[이번 일 — 지원서 채우기]
{url} 을 navigate 로 열고 snapshot 으로 화면을 본 뒤 지원서 칸을 채운다.
'다음' 같은 단계 이동은 click 으로 넘어가고, 새 화면마다 snapshot 을 다시 뜬다.
모든 칸을 채웠으면 최종 제출 버튼의 ref 로 ready_for_review 를 부른다. run 은 거기서 끝난다."""

_NO_GUIDE = "(없음)"


def build_fill_system_prompt(
    *,
    url: str,
    domain: str,
    profile: Profile | None,
    global_guide: str = "",
    domain_guide: str = "",
) -> str:
    """역할·규칙 + 전역/도메인 가이드 + fill 지시 + 프로필 요약."""
    return "\n\n".join(
        (
            _ROLE,
            _RULES,
            f"[가이드 — 전역]\n{global_guide.strip() or _NO_GUIDE}",
            f"[가이드 — {domain}]\n{domain_guide.strip() or _NO_GUIDE}",
            _FILL_TASK.format(url=url),
            f"[프로필]\n{profile_summary(profile)}",
        )
    )


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
