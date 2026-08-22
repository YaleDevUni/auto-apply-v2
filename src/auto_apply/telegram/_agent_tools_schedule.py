"""공고 수집/자동 지원 Schedule 관리 도구 — `telegram/_agent_tools.py`가 `TOOLS`에 병합한다.

"공고수집 및 지원하기 스케줄링" 요청(2026-08-22)으로 신설. 두 Schedule(`schedule.py`, cron)
모두 등록 자체는 `cli.py collect-schedule`/`apply-schedule`로 이미 되지만, 봇에서 켜고 끄고
상태를 볼 수 있는 통로가 없었다. cron 시각·건수(APPLY_SCHEDULE_COUNT 등)는 여기서 바꾸지
않는다 — `.env`로 고정한 값이고, 봇은 그 값으로 등록된 Schedule을 pause/unpause 하는
on/off 스위치만 쥔다(2026-08-22 사용자 결정 — 채팅으로 cron 표현식을 파싱시키면 실패·오해석
위험이 커진다는 판단, `set_schedule_enabled`가 실제 cron/count 는 그대로 두고 상태만 바꾸는
이유). `_agent_tools.py`처럼 새 파일로 분리하는 이유도 같다(§ CLAUDE.md "한 파일 = 한 책임") —
그 파일이 이미 200줄을 넘어가고 있었다.

`ApplyIntakeWorkflow`/`ApplyIntakeActivities`가 실제로 자동 지원을 시작하는 워크플로우를
`start_actionable_applications` 도구(telegram/_agent_tools.py)와 공유하지만, 최종 제출은
그 워크플로우 안의 텔레그램 승인 뒤에서만 일어난다는 불변식은 여기서도 그대로다 — 이 파일이
켜는 건 "언제 지원 프로세스를 자동으로 시작할지"일 뿐이다.
"""

from collections.abc import Awaitable, Callable

from temporalio.client import Client
from temporalio.service import RPCError

from auto_apply.bootstrap import Container
from auto_apply.schedule import (
    APPLY_INTAKE_SCHEDULE_ID,
    JOB_COLLECTION_SCHEDULE_ID,
    ensure_apply_intake_schedule,
    ensure_job_collection_schedule,
)

ToolHandler = Callable[[dict[str, str], Container, Client], Awaitable[str]]

_TARGETS = {
    "collection": (JOB_COLLECTION_SCHEDULE_ID, ensure_job_collection_schedule, "공고 수집"),
    "apply": (APPLY_INTAKE_SCHEDULE_ID, ensure_apply_intake_schedule, "자동 지원"),
}


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"true", "1", "yes", "y", "on"}


async def _describe_line(
    client: Client, schedule_id: str, label: str, cron: str, extra: str
) -> str:
    # cron 은 Temporal 서버에 물어보지 않고 우리가 등록에 쓴 설정값(c.settings)을 그대로
    # 보여준다 — 서버가 describe() 에서 돌려주는 `spec.cron_expressions`는 등록한 cron 표현식을
    # 내부 캘린더 스펙으로 컴파일하며 비워버려서(실측, 라이브 등록 후 describe) 그대로 읽으면
    # 늘 "-"만 보인다.
    try:
        desc = await client.get_schedule_handle(schedule_id).describe()
    except RPCError:
        return f"{label}: 등록 안 됨 (cron='{cron}', {extra})"
    state = "꺼짐" if desc.schedule.state.paused else "켜짐"
    times = desc.info.next_action_times
    next_at = times[0].isoformat() if times else "-"
    return f"{label}: {state}, cron='{cron}', 다음 실행={next_at}, {extra}"


async def _schedule_status(_args: dict[str, str], c: Container, client: Client) -> str:
    rows = [
        (
            JOB_COLLECTION_SCHEDULE_ID,
            "공고 수집",
            c.settings.job_collection_cron,
            f"platforms={c.settings.job_collection_platforms}",
        ),
        (
            APPLY_INTAKE_SCHEDULE_ID,
            "자동 지원",
            c.settings.apply_schedule_cron,
            f"count={c.settings.apply_schedule_count}",
        ),
    ]
    lines = []
    for schedule_id, label, cron, extra in rows:
        lines.append(await _describe_line(client, schedule_id, label, cron, extra))
    return "\n".join(lines)


async def _set_schedule_enabled(args: dict[str, str], c: Container, client: Client) -> str:
    entry = _TARGETS.get(args.get("target", "").strip())
    if entry is None:
        return "target 은 'collection'(공고 수집) 또는 'apply'(자동 지원) 여야 합니다."
    schedule_id, ensure_fn, label = entry
    if _parse_bool(args.get("enabled", "")):
        # idempotent — 없으면 만들고 있으면 최신 설정(.env)으로 덮어쓴다. pause 상태였어도
        # update 는 paused 여부를 안 건드리므로 이어서 명시적으로 unpause 한다.
        await ensure_fn(client, c.settings)
        await client.get_schedule_handle(schedule_id).unpause(note="텔레그램 채팅에서 켬")
        return f"{label} 스케줄을 켰습니다."
    try:
        await client.get_schedule_handle(schedule_id).pause(note="텔레그램 채팅에서 끔")
    except RPCError:
        return f"{label} 스케줄은 등록돼 있지 않습니다 — 이미 꺼진 것과 같습니다."
    return f"{label} 스케줄을 껐습니다."


SCHEDULE_TOOLS: dict[str, tuple[str, tuple[str, ...], ToolHandler]] = {
    "schedule_status": (
        "공고 수집/자동 지원 두 Schedule 의 현재 상태(켜짐/꺼짐, cron, 다음 실행 시각)를 본다."
        " '스케줄 상태 알려줘', '언제 도는지 알려줘' 같은 요청에 쓴다",
        (),
        _schedule_status,
    ),
    "set_schedule_enabled": (
        "공고 수집(target=collection) 또는 자동 지원(target=apply) Schedule 을 켜거나 끈다"
        "(enabled=true/false). cron 시각·건수는 서버 설정(.env)에 고정돼 있어 여기서 바꾸지"
        " 못한다 — on/off 만 가능하다. '자동지원 스케줄 꺼줘', '공고수집 스케줄 켜줘' 같은"
        " 요청에 쓴다",
        ("target", "enabled"),
        _set_schedule_enabled,
    ),
}
