"""공고 수집/자동 지원 Schedule 설정 — DB(`ScheduleConfig`)와 Temporal Schedule을 잇는다

(§ apply-schedule). "cron으로 하지말고 서버에서 하면 설정파일 건드릴 필요 없다"(2026-08-22
사용자 요청)로, 시각/건수는 더 이상 `.env`가 유일한 원천이 아니다 — `.env`(`job_collection_cron`
등)는 DB에 아직 행이 없을 때만 쓰는 **최초 시드값**이고, 한 번이라도 저장되면 그 뒤로는 DB가
그 target의 유일한 원천이다.

`cli.py`/`watchdog.py`처럼 Temporal Client SDK를 직접 쓰는 운영 진입점이다(workflow 파일이
아니므로 §11.3 대상 아님) — 텔레그램 채팅 도구(`telegram/_agent_tools_schedule.py`)와
`cli.py collect-schedule`/`apply-schedule` 둘 다 이 모듈을 통해서만 Schedule 을 만지므로,
"DB에 쓰고 Temporal에 반영"이 항상 같이 일어난다는 보장이 이 파일 하나에 있다.
"""

from temporalio.client import Client

from auto_apply.bootstrap import Container
from auto_apply.config import Settings
from auto_apply.contracts.dto import ScheduleConfig
from auto_apply.domain.schedule_cron import build_cron
from auto_apply.schedule import ensure_apply_intake_schedule, ensure_job_collection_schedule


def _seed(cfg: Settings, target: str) -> ScheduleConfig:
    """DB에 아직 행이 없는 target의 최초 기본값 — `.env`에서만 가져온다."""
    if target == "collection":
        hour, minute = _split_cron(cfg.job_collection_cron, default=(9, 0))
        platforms = [p.strip() for p in cfg.job_collection_platforms.split(",") if p.strip()]
        return ScheduleConfig(target="collection", hour=hour, minute=minute, platforms=platforms)
    hour, minute = _split_cron(cfg.apply_schedule_cron, default=(10, 0))
    return ScheduleConfig(target="apply", hour=hour, minute=minute, count=cfg.apply_schedule_count)


def _split_cron(cron: str, *, default: tuple[int, int]) -> tuple[int, int]:
    """`"M H * * *"` → (hour, minute). `.env` 시드값 파싱 전용 — 형식이 그 값이 아니면(사람이

    직접 임의 cron을 넣은 경우) 기본값으로 물러선다. 채팅 입력은 여기로 안 온다(이미 hour/minute
    정수로 받는다, `domain/schedule_cron.py`).
    """
    parts = cron.split()
    if len(parts) == 5:
        try:
            return int(parts[1]), int(parts[0])
        except ValueError:
            pass
    return default


async def load_or_seed(c: Container, target: str) -> ScheduleConfig:
    """DB에 있으면 그대로, 없으면 `.env` 시드값으로 만들어 저장한 뒤 돌려준다."""
    async with c.uow() as uow:
        existing = await uow.schedule_config.get(target)
    if existing is not None:
        return existing
    seeded = _seed(c.settings, target)
    async with c.uow() as uow:
        await uow.schedule_config.set(seeded)
        await uow.commit()
    return seeded


async def push(client: Client, config: ScheduleConfig) -> str:
    """이미 저장된 `ScheduleConfig`를 Temporal Schedule에 반영한다. 반환값은 schedule.py 의

    "created"|"updated".
    """
    cron = build_cron(config.hour, config.minute)
    if config.target == "collection":
        return await ensure_job_collection_schedule(client, cron, config.platforms or [])
    return await ensure_apply_intake_schedule(client, cron, config.count or 1)


async def save_and_push(c: Container, client: Client, config: ScheduleConfig) -> str:
    """DB에 먼저 쓰고(그래야 재조회·재시작에도 값이 남는다) Temporal에 반영한다."""
    async with c.uow() as uow:
        await uow.schedule_config.set(config)
        await uow.commit()
    return await push(client, config)


async def ensure(c: Container, client: Client, target: str) -> tuple[str, ScheduleConfig]:
    """DB에 없으면 `.env` 시드값으로 만들고, 있는 값 그대로 Temporal에 반영한다.

    `cli.py collect-schedule`/`apply-schedule`과 텔레그램 `set_schedule_enabled(enabled=true)`가
    쓴다 — 둘 다 "지금 저장된(또는 처음이면 시드) 설정으로 Schedule을 켜라"는 같은 의도다.
    """
    config = await load_or_seed(c, target)
    outcome = await push(client, config)
    return outcome, config
