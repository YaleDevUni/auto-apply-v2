"""Temporal worker 엔트리포인트.

Task queue 를 2개로 나눈다: default / ai. 큐마다 워커의 자원 특성이 다르므로 동시성도
따로 잡는다. T0.2 에서 Temporal 과 함께 삭제된다.
"""

import argparse
import asyncio
from collections.abc import Callable, Sequence
from typing import Any, Literal, cast

import structlog
from temporalio.client import Client
from temporalio.worker import Worker

from auto_apply.activities.ping import PingActivities
from auto_apply.activities.resume import ResumeActivities
from auto_apply.bootstrap import Container, build_container
from auto_apply.config import load_settings
from auto_apply.temporal_config import DATA_CONVERTER
from auto_apply.workflows.ping import PingWorkflow
from auto_apply.workflows.resume import ResumeWorkflow

log = structlog.get_logger(__name__)

Queue = Literal["default", "ai"]

MAX_CONCURRENT: dict[Queue, int] = {"default": 50, "ai": 5}


def _registrations(
    queue: Queue, c: Container
) -> tuple[Sequence[type], Sequence[Callable[..., Any]]]:
    match queue:
        case "default":
            return [PingWorkflow], [*PingActivities(c.clock, c.store).all()]
        case "ai":
            return (
                [ResumeWorkflow],
                [*ResumeActivities(c.generator, c.reviewer, c.pdf).all()],
            )


async def main() -> None:
    parser = argparse.ArgumentParser(prog="auto-apply-worker")
    parser.add_argument("--queue", choices=["default", "ai"], default="default")
    args = parser.parse_args()
    queue = cast(Queue, args.queue)

    cfg = load_settings()
    container = build_container(cfg)
    client = await Client.connect(
        cfg.temporal_address, namespace=cfg.temporal_namespace, data_converter=DATA_CONVERTER
    )
    workflows, activities = _registrations(queue, container)

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
