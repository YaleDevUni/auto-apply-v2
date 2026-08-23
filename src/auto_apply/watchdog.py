"""워크플로우 능동 감시 watchdog (workflow-failure-visibility-backlog §3).

`_execute()`의 `load_active_recipe` 처럼, 개별 실패 지점을 workflow 코드 안에서 try/except로
막는 게 1차 방어선이다(§ workflows/application.py). 그게 Temporal 커뮤니티의 표준 권고이기도
하다 — "워크플로우 실패를 감지하려면 그 안에서 잡아라"(maxim,
https://community.temporal.io/t/sending-notification-when-the-workflow-has-failed/14701).
하지만 그건 *알고 있는* 실패 지점에만 통한다. 앞으로 또 놓칠 수 있는 지점(코드 버그, 사람의
실수로 인한 `terminate`, `workflow_execution_timeout`처럼 워크플로우 코드가 아예 더 못
도는 종료)까지 잡으려면 프로세스 밖에서 Temporal의 visibility API를 폴링하는 수밖에 없다 —
이것도 커뮤니티에서 "실시간은 아니지만 유일한 외부 감지 수단"으로 확인했다
(https://community.temporal.io/t/is-it-possible-to-listen-for-workflow-failures/6843).
Standard(SQL) visibility도 `ExecutionStatus IN (...)`/`CloseTime` 필터를 지원해서
(https://docs.temporal.io/list-filter) Elasticsearch 없이 이 구성으로 충분하다.

`cli.py`/`schedule.py`처럼 Temporal Client SDK를 직접 쓰는 운영 진입점이다(§11.3 대상 아님,
workflow 파일이 아니다) — 새 port를 만들지 않는다. 알림은 이미 있는 `Notifier` port를
그대로 쓴다(§11.6, "Telegram"이라는 단어를 모르는 채널 추상화).

재시작 사이의 워터마크(`since`)를 영속화하지 않는다 — `telegram/listener.py`의 offset과 같은
이유다. 재시작 직후에는 `WATCHDOG_LOOKBACK_MINUTES`만큼 과거를 다시 훑어 중복 알림이 최대
그 창(window) 안에서만 날 수 있는데, 실패를 몇 번 더 알리는 비용이 놓치는 비용보다 훨씬
싸다(감시의 존재 이유 자체가 "아무도 못 봤을 때"다).
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from temporalio.client import Client, WorkflowExecution

from auto_apply.bootstrap import build_container
from auto_apply.config import load_settings
from auto_apply.contracts.dto import NotifyEvent
from auto_apply.ports.notifier import Notifier
from auto_apply.process_alerts import notify_safely, run_guarded
from auto_apply.temporal_config import DATA_CONVERTER

log = structlog.get_logger(__name__)

_BAD_STATUSES = ("Failed", "Terminated", "TimedOut")


@dataclass(frozen=True)
class WatchdogHit:
    workflow_id: str
    run_id: str
    status: str
    close_time: datetime | None


def build_query(since: datetime) -> str:
    """순수 함수라 Temporal 없이 바로 검증한다(§ test_schedule.py와 같은 패턴)."""
    statuses = ", ".join(f"'{s}'" for s in _BAD_STATUSES)
    return f"ExecutionStatus IN ({statuses}) AND CloseTime > '{since.isoformat()}'"


def to_hit(execution: WorkflowExecution) -> WatchdogHit:
    return WatchdogHit(
        workflow_id=execution.id,
        run_id=execution.run_id,
        status=execution.status.name if execution.status else "UNKNOWN",
        close_time=execution.close_time,
    )


def blind_alert(consecutive_failures: int, threshold: int, error: str) -> str | None:
    """폴링이 연속으로 실패하면 "감시가 눈이 먼 상태"다 — 그것도 알려야 한다.

    폴링 실패는 로그에만 남기고 계속 도는 게 맞다(모듈 docstring: 감시가 감시 대상이 되면
    안 된다). 하지만 Temporal 접속이 몇 분째 안 되는 동안에는 워크플로우가 죽어도 알림이
    안 온다 — 그 침묵이 "아무 문제 없음"으로 읽히는 게 제일 위험하다. 임계치에 정확히
    도달한 순간 딱 한 번만 알린다(계속 실패하는 동안 매 주기 알리면 그게 소음이다).
    """
    if consecutive_failures != threshold:
        return None
    return (
        f"워크플로우 감시가 {threshold}회 연속 실패했다 — 지금은 실패한 워크플로우를 감지하지"
        f" 못하는 상태다.\nTemporal 접속을 확인해라: {error}"
    )


def format_message(hit: WatchdogHit) -> str:
    closed_at = hit.close_time.isoformat() if hit.close_time else "?"
    return (
        f"워크플로우가 {hit.status} 로 끝났다\n"
        f"id={hit.workflow_id} run={hit.run_id}\n"
        f"closed_at={closed_at}\n"
        "Temporal UI 에서 이력을 확인해라 (auto-apply status 는 application-* 만 조회 가능)."
    )


async def poll_once(
    client: Client, notifier: Notifier, since: datetime, seen: set[tuple[str, str]]
) -> datetime:
    """새로 발견한(=`seen`에 없는) 불건강 워크플로우를 알리고, 다음 폴링의 워터마크를 반환한다."""
    watermark = since
    async for execution in client.list_workflows(build_query(since)):
        hit = to_hit(execution)
        key = (hit.workflow_id, hit.run_id)
        if key in seen:
            if hit.close_time and hit.close_time > watermark:
                watermark = hit.close_time
            continue
        log.warning(
            "watchdog.unhealthy_workflow",
            workflow_id=hit.workflow_id,
            run_id=hit.run_id,
            status=hit.status,
        )
        # notify()가 실패하면 여기서 예외가 위로 던져진다 — seen/watermark 를 그 *뒤*에
        # 갱신하는 게 핵심이다. notify 전에 seen 에 넣어버리면, 알림이 안 나갔는데도 다음
        # 폴링이 "이미 처리함"으로 skip 해서 알림이 영영 유실된다(그 폴링 자체는 예외 없이
        # 끝나서 바깥의 연속 실패 카운트도 리셋돼 blind_alert 임계치에도 안 닿는다).
        # 실패한 hit 은 seen 에도 안 들어가고 watermark 도 안 전진하므로, 다음 폴링에서
        # (같은 since 이하 구간이 다시 조회되어) 그대로 재시도된다.
        await notifier.notify(NotifyEvent(kind="WORKFLOW_UNHEALTHY", message=format_message(hit)))
        seen.add(key)
        if hit.close_time and hit.close_time > watermark:
            watermark = hit.close_time
    return watermark


async def main() -> None:
    cfg = load_settings()
    container = build_container(cfg)
    client = await Client.connect(
        cfg.temporal_address, namespace=cfg.temporal_namespace, data_converter=DATA_CONVERTER
    )
    log.info(
        "watchdog.start",
        poll_interval_s=cfg.watchdog_poll_interval_seconds,
        lookback_min=cfg.watchdog_lookback_minutes,
    )

    since = datetime.now(UTC) - timedelta(minutes=cfg.watchdog_lookback_minutes)
    seen: set[tuple[str, str]] = set()
    failures = 0
    while True:
        try:
            since = await poll_once(client, container.notifier, since, seen)
        except Exception as e:
            # telegram/listener.py 에서 실측한 교훈과 같다 — 폴링 중 뭐가 터져도 이 프로세스는
            # 살아 있어야 한다. 안 그러면 감시가 감시 대상이 되는 역설이 생긴다.
            log.exception("watchdog.poll_failed")
            failures += 1
            message = blind_alert(failures, cfg.watchdog_blind_alert_after, str(e))
            if message is not None:
                await notify_safely(
                    container.notifier, NotifyEvent(kind="WATCHDOG_BLIND", message=message)
                )
        else:
            if failures >= cfg.watchdog_blind_alert_after:
                await notify_safely(
                    container.notifier,
                    NotifyEvent(kind="WATCHDOG_RECOVERED", message="워크플로우 감시가 복구됐다."),
                )
            failures = 0
        await asyncio.sleep(cfg.watchdog_poll_interval_seconds)


if __name__ == "__main__":
    # watchdog 이 죽으면 아무도 안 알려준다 — 그래서 이 프로세스야말로 크래시 알림이 필요하다
    # (§ process_alerts.py).
    asyncio.run(run_guarded("watchdog", main))
