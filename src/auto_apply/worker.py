"""Temporal worker 엔트리포인트.

Task queue 를 3개로 나눈다 (ARCHITECTURE.md §1): default / ai / browser
"""

import argparse
import asyncio
from collections.abc import Callable, Sequence
from typing import Any, Literal, cast

import structlog
from temporalio.client import Client
from temporalio.worker import Worker

from auto_apply.activities.ping import PingActivities
from auto_apply.bootstrap import Container, build_container
from auto_apply.config import load_settings
from auto_apply.workflows.ping import PingWorkflow

log = structlog.get_logger(__name__)

Queue = Literal["default", "ai", "browser"]


def _registrations(
    queue: Queue, c: Container
) -> tuple[Sequence[type], Sequence[Callable[..., Any]]]:
    """큐별 등록 목록. 큐를 나누는 이유는 워커의 자원 특성이 다르기 때문이다."""
    match queue:
        case "default":
            return [PingWorkflow], [*PingActivities(c.clock, c.store).all()]
        case "ai":
            return [], []  # M3
        case "browser":
            return [], []  # M2


async def main() -> None:
    parser = argparse.ArgumentParser(prog="auto-apply-worker")
    parser.add_argument("--queue", choices=["default", "ai", "browser"], default="default")
    args = parser.parse_args()
    queue = cast(Queue, args.queue)

    cfg = load_settings()
    container = build_container(cfg)
    workflows, activities = _registrations(queue, container)

    if not workflows and not activities:
        log.warning("worker.empty", queue=queue, hint="이 큐는 아직 구현 전이다 (M2/M3)")
        return

    client = await Client.connect(cfg.temporal_address, namespace=cfg.temporal_namespace)
    log.info(
        "worker.start",
        queue=queue,
        address=cfg.temporal_address,
        workflows=[w.__name__ for w in workflows],
        activities=len(activities),
    )

    async with Worker(client, task_queue=queue, workflows=workflows, activities=activities):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
