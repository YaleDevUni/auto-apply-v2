"""운영 CLI.

Telegram 이 붙기 전까지 사람이 워크플로우를 조작하는 통로다 (ARCHITECTURE.md §6).
여기서 하는 일은 전부 "워크플로우에 signal 을 보내는 것" 이며 DB 를 직접 건드리지 않는다.
Telegram 핸들러도 같은 규칙을 따른다.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from temporalio.client import Client

from auto_apply.config import load_settings
from auto_apply.contracts.dto import (
    ApproveSignal,
    RejectSignal,
    RescheduleSignal,
    StartApplication,
)
from auto_apply.temporal_config import DATA_CONVERTER, QUEUE_DEFAULT
from auto_apply.workflows.application import ApplicationWorkflow


def _parse_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _client() -> Client:
    cfg = load_settings()
    return await Client.connect(
        cfg.temporal_address, namespace=cfg.temporal_namespace, data_converter=DATA_CONVERTER
    )


async def _run(args: argparse.Namespace) -> None:
    client = await _client()
    cfg = load_settings()
    wf_id = f"application-{args.id}"

    match args.command:
        case "start":
            handle = await client.start_workflow(
                ApplicationWorkflow.run,
                StartApplication(
                    application_id=args.id,
                    user_id=args.user,
                    job_url=args.job_url,
                    approval_timeout_hours=cfg.approval_timeout_hours,
                    dry_run_only=cfg.dry_run_only,
                ),
                id=wf_id,  # workflow_id 가 곧 멱등성 키다 (§2.1)
                task_queue=QUEUE_DEFAULT,
            )
            print(f"started: {handle.id} (run {handle.result_run_id})")
        case "status":
            view = await client.get_workflow_handle(wf_id).query(ApplicationWorkflow.state)
            print(
                f"{args.id}: {view.state} scheduled_at={view.scheduled_at} attempts={view.attempts}"
            )
        case "approve":
            await client.get_workflow_handle(wf_id).signal(
                ApplicationWorkflow.approve, ApproveSignal(scheduled_at=_parse_at(args.at))
            )
            print(f"approved: {args.id} at={args.at or 'now'}")
        case "reject":
            await client.get_workflow_handle(wf_id).signal(
                ApplicationWorkflow.reject, RejectSignal(reason=args.reason)
            )
            print(f"rejected: {args.id}")
        case "schedule":
            at = _parse_at(args.at)
            assert at is not None
            await client.get_workflow_handle(wf_id).signal(
                ApplicationWorkflow.reschedule, RescheduleSignal(scheduled_at=at)
            )
            print(f"rescheduled: {args.id} → {at}")
        case "cancel":
            await client.get_workflow_handle(wf_id).signal(ApplicationWorkflow.cancel)
            print(f"cancelled: {args.id}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="auto-apply")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="지원 워크플로우 시작")
    start.add_argument("id")
    start.add_argument("--job-url", required=True)
    start.add_argument("--user", default="u1")

    for name, help_text in [("status", "현재 단계 조회"), ("cancel", "취소")]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("id")

    approve = sub.add_parser("approve", help="승인 (+ 예약)")
    approve.add_argument("id")
    approve.add_argument("--at", help="ISO8601, 예: 2026-08-20T09:00")

    reject = sub.add_parser("reject", help="거절")
    reject.add_argument("id")
    reject.add_argument("--reason", default="")

    schedule = sub.add_parser("schedule", help="예약 시각 재조정")
    schedule.add_argument("id")
    schedule.add_argument("--at", required=True)

    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
