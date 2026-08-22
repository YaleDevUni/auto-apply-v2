"""공고 수집/자동 지원 Schedule 관리 도구 — `telegram/_agent_tools.py`가 `TOOLS`에 병합한다.

"공고수집 및 지원하기 스케줄링" 요청(2026-08-22)으로 신설. 처음엔 시각/건수를 `.env`에
고정하고 채팅은 on/off만 지원했는데, 곧바로 사용자가 "cron으로 하지 말고 서버에서 하면
설정파일 건드릴 필요도 없지 않냐"고 정정했다 — 그래서 시각/건수도 채팅으로 바꾼다. 다만
LLM이 cron 문법 자체를 만들게 하지는 않는다(실패·오해석 위험) — `set_schedule_time`은
hour/minute/count를 정수 그대로 받고, cron 조립은 `domain/schedule_cron.py`(순수 함수)가
한다(Recipe/가이드 patch와 같은 "AI는 생성만, 조합은 코드" 철학). 값 자체는
`ScheduleConfigRepository`(DB, `schedule_config.py`)에 저장되고 거기서 Temporal Schedule로
반영된다 — `.env`는 DB에 아직 값이 없는 최초 1회에만 쓰는 시드다.

`_agent_tools.py`가 아니라 새 파일로 분리하는 이유는 그 파일이 이미 200줄을 넘어가고
있었기 때문이다(§ CLAUDE.md "한 파일 = 한 책임").

`ApplyIntakeWorkflow`/`ApplyIntakeActivities`가 실제로 자동 지원을 시작하는 워크플로우를
`start_actionable_applications` 도구(telegram/_agent_tools.py)와 공유하지만, 최종 제출은
그 워크플로우 안의 텔레그램 승인 뒤에서만 일어난다는 불변식은 여기서도 그대로다 — 이 파일이
켜는 건 "언제 지원 프로세스를 자동으로 시작할지"일 뿐이다.
"""

from collections.abc import Awaitable, Callable

from temporalio.client import Client
from temporalio.service import RPCError

from auto_apply.bootstrap import Container
from auto_apply.contracts.dto import ScheduleConfig
from auto_apply.schedule import APPLY_INTAKE_SCHEDULE_ID, JOB_COLLECTION_SCHEDULE_ID
from auto_apply.schedule_config import ensure as ensure_schedule
from auto_apply.schedule_config import load_or_seed, save_and_push

ToolHandler = Callable[[dict[str, str], Container, Client], Awaitable[str]]

_SCHEDULE_IDS = {"collection": JOB_COLLECTION_SCHEDULE_ID, "apply": APPLY_INTAKE_SCHEDULE_ID}
_LABELS = {"collection": "공고 수집", "apply": "자동 지원"}


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"true", "1", "yes", "y", "on"}


def _extra(config: ScheduleConfig) -> str:
    return f"count={config.count}" if config.target == "apply" else f"platforms={config.platforms}"


async def _describe_line(client: Client, target: str, config: ScheduleConfig) -> str:
    label = _LABELS[target]
    time = f"{config.hour:02d}:{config.minute:02d}"
    try:
        desc = await client.get_schedule_handle(_SCHEDULE_IDS[target]).describe()
    except RPCError:
        return f"{label}: 등록 안 됨 (시각={time}, {_extra(config)})"
    state = "꺼짐" if desc.schedule.state.paused else "켜짐"
    times = desc.info.next_action_times
    next_at = times[0].isoformat() if times else "-"
    return f"{label}: {state}, 시각={time}, 다음 실행={next_at}, {_extra(config)}"


async def _schedule_status(_args: dict[str, str], c: Container, client: Client) -> str:
    lines = []
    for target in ("collection", "apply"):
        config = await load_or_seed(c, target)
        lines.append(await _describe_line(client, target, config))
    return "\n".join(lines)


async def _set_schedule_enabled(args: dict[str, str], c: Container, client: Client) -> str:
    target = args.get("target", "").strip()
    if target not in _SCHEDULE_IDS:
        return "target 은 'collection'(공고 수집) 또는 'apply'(자동 지원) 여야 합니다."
    label = _LABELS[target]
    schedule_id = _SCHEDULE_IDS[target]
    if _parse_bool(args.get("enabled", "")):
        # DB에 있으면 그 값, 없으면 .env 시드값으로 처음 등록한 뒤 켠다.
        await ensure_schedule(c, client, target)
        await client.get_schedule_handle(schedule_id).unpause(note="텔레그램 채팅에서 켬")
        return f"{label} 스케줄을 켰습니다."
    try:
        await client.get_schedule_handle(schedule_id).pause(note="텔레그램 채팅에서 끔")
    except RPCError:
        return f"{label} 스케줄은 등록돼 있지 않습니다 — 이미 꺼진 것과 같습니다."
    return f"{label} 스케줄을 껐습니다."


async def _set_schedule_time(args: dict[str, str], c: Container, client: Client) -> str:
    target = args.get("target", "").strip()
    if target not in _SCHEDULE_IDS:
        return "target 은 'collection'(공고 수집) 또는 'apply'(자동 지원) 여야 합니다."
    try:
        hour = int(args.get("hour", ""))
        minute = int(args.get("minute", "") or "0")
    except ValueError:
        return "hour/minute 은 숫자여야 합니다(예: hour=14, minute=30)."
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return "hour 는 0~23, minute 은 0~59 여야 합니다."

    current = await load_or_seed(c, target)
    count = current.count
    if target == "apply":
        raw_count = args.get("count", "").strip()
        if raw_count:
            try:
                count = max(1, int(raw_count))
            except ValueError:
                return "count 는 숫자여야 합니다."
    config = ScheduleConfig(
        target=target, hour=hour, minute=minute, count=count, platforms=current.platforms
    )
    outcome = await save_and_push(c, client, config)
    label = _LABELS[target]
    return f"{label} 스케줄을 {hour:02d}:{minute:02d} 로 바꿨습니다({_extra(config)}, {outcome})."


SCHEDULE_TOOLS: dict[str, tuple[str, tuple[str, ...], ToolHandler]] = {
    "schedule_status": (
        "공고 수집/자동 지원 두 Schedule 의 현재 상태(켜짐/꺼짐, 시각, 다음 실행 시각, 건수/"
        "플랫폼)를 본다. '스케줄 상태 알려줘', '언제 도는지 알려줘' 같은 요청에 쓴다",
        (),
        _schedule_status,
    ),
    "set_schedule_enabled": (
        "공고 수집(target=collection) 또는 자동 지원(target=apply) Schedule 을 켜거나 끈다"
        "(enabled=true/false) — 시각/건수는 그대로 두고 on/off 만 바꾼다. '자동지원 스케줄"
        " 꺼줘', '공고수집 스케줄 켜줘' 같은 요청에 쓴다",
        ("target", "enabled"),
        _set_schedule_enabled,
    ),
    "set_schedule_time": (
        "공고 수집(target=collection) 또는 자동 지원(target=apply) Schedule 의 실행 시각"
        "(hour 0~23, minute 0~59, 생략시 0)을 바꾼다. target=apply 면 count(건수)도 같이"
        " 바꿀 수 있다(생략하면 기존 값 유지) — target=collection 의 platforms 는 채팅으로"
        " 못 바꾼다. '자동지원 오후 2시 5건으로 바꿔줘', '공고수집 아침 8시로 옮겨줘' 같은"
        " 요청에 쓴다. 저장은 즉시 반영되고(.env 를 건드릴 필요 없다) 꺼져 있던 스케줄은"
        " 시각만 바뀌고 계속 꺼진 채로 남는다 — 다시 켜려면 set_schedule_enabled 를 따로 써야"
        " 한다",
        ("target", "hour", "minute", "count"),
        _set_schedule_time,
    ),
}
