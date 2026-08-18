"""Temporal worker 엔트리포인트.

Task queue 를 3개로 나눈다 (ARCHITECTURE.md §1): default / ai / browser
큐마다 워커의 자원 특성이 다르므로 동시성도 따로 잡는다.
"""

import argparse
import asyncio
from collections.abc import Callable, Sequence
from typing import Any, Literal, cast

import structlog
from temporalio.client import Client
from temporalio.worker import Worker

from auto_apply.activities.application import ApplicationActivities
from auto_apply.activities.browser import BrowserActivities
from auto_apply.activities.ping import PingActivities
from auto_apply.activities.resume import ResumeActivities
from auto_apply.bootstrap import Container, build_container
from auto_apply.config import load_settings
from auto_apply.temporal_config import DATA_CONVERTER
from auto_apply.workflows.application import ApplicationWorkflow
from auto_apply.workflows.ping import PingWorkflow
from auto_apply.workflows.resume import ResumeWorkflow

log = structlog.get_logger(__name__)

Queue = Literal["default", "ai", "browser"]

# browser 워커는 세션당 메모리가 크고 플랫폼 rate limit 이 있어 동시성을 낮게 잡는다 (§1)
MAX_CONCURRENT: dict[Queue, int] = {"default": 50, "ai": 5, "browser": 1}


def _registrations(
    queue: Queue, c: Container
) -> tuple[Sequence[type], Sequence[Callable[..., Any]]]:
    match queue:
        case "default":
            return (
                [ApplicationWorkflow, PingWorkflow],
                [
                    *ApplicationActivities(c.registry, c.notifier, c.recipes, c.uow).all(),
                    *PingActivities(c.clock, c.store).all(),
                ],
            )
        case "ai":
            return (
                [ResumeWorkflow],
                [*ResumeActivities(c.generator, c.reviewer, c.pdf).all()],
            )
        case "browser":
            return [], [*BrowserActivities(c.executor).all()]


async def main() -> None:
    parser = argparse.ArgumentParser(prog="auto-apply-worker")
    parser.add_argument("--queue", choices=["default", "ai", "browser"], default="default")
    args = parser.parse_args()
    queue = cast(Queue, args.queue)

    cfg = load_settings()
    container = build_container(cfg)
    workflows, activities = _registrations(queue, container)

    client = await Client.connect(
        cfg.temporal_address, namespace=cfg.temporal_namespace, data_converter=DATA_CONVERTER
    )
    log.info(
        "worker.start",
        queue=queue,
        address=cfg.temporal_address,
        workflows=[w.__name__ for w in workflows],
        activities=len(activities),
        dry_run_only=cfg.dry_run_only,
    )

    async with Worker(
        client,
        task_queue=queue,
        workflows=workflows,
        activities=activities,
        max_concurrent_activities=MAX_CONCURRENT[queue],
    ):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
