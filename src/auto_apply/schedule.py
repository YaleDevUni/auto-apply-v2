"""`JobCollectionWorkflow`를 주기 실행하는 Temporal Schedule (ARCHITECTURE.md §11.2b).

지금까지는 `cli.py collect`로 수동 트리거만 됐다. 운영에 올릴 때 `collect-schedule` 서브커맨드로
한 번 등록하면 그 뒤로는 cron이 대신 시작해준다. 등록은 create-or-update로 만들어 몇 번을 실행해도
안전하다(idempotent) — 이미 있으면 최신 설정으로 덮어쓴다.

workflow 파일이 아니므로 §11.3의 "workflow는 contracts/domain만 본다" 규칙 대상이 아니다.
`cli.py`처럼 Temporal client SDK를 직접 쓰는 운영 진입점이다.
"""

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
)

from auto_apply.config import Settings
from auto_apply.contracts.dto import ApplyIntakeInput
from auto_apply.contracts.job import CollectJobsInput
from auto_apply.temporal_config import QUEUE_DEFAULT
from auto_apply.workflows.apply_intake import ApplyIntakeWorkflow
from auto_apply.workflows.job_collection import JobCollectionWorkflow

JOB_COLLECTION_SCHEDULE_ID = "job-collection-schedule"
APPLY_INTAKE_SCHEDULE_ID = "apply-intake-schedule"


def build_job_collection_schedule(cfg: Settings) -> Schedule:
    """등록될 Schedule 정의. 순수 함수라 Temporal 서버 없이도 테스트할 수 있다."""
    platforms = [p.strip() for p in cfg.job_collection_platforms.split(",") if p.strip()]
    return Schedule(
        action=ScheduleActionStartWorkflow(
            JobCollectionWorkflow.run,
            CollectJobsInput(platforms=platforms),
            id=f"{JOB_COLLECTION_SCHEDULE_ID}-run",
            task_queue=QUEUE_DEFAULT,
        ),
        spec=ScheduleSpec(cron_expressions=[cfg.job_collection_cron]),
        # 이전 실행이 안 끝났으면 겹쳐 돌리지 않고 건너뛴다 — collect_platform_jobs 의
        # 재시도 단위가 "플랫폼 전체"라, 겹쳐 돌면 같은 공고를 두 activity가 동시에
        # upsert할 수 있다(§11.2b). JobRepository.upsert가 멱등이라 데이터가 깨지진
        # 않지만, 굳이 중복 실행을 허용할 이유가 없다.
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def ensure_job_collection_schedule(client: Client, cfg: Settings) -> str:
    """없으면 만들고, 있으면 최신 설정으로 덮어쓴다. 반환값은 "created" | "updated"."""
    schedule = build_job_collection_schedule(cfg)
    try:
        await client.create_schedule(JOB_COLLECTION_SCHEDULE_ID, schedule)
        return "created"
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(JOB_COLLECTION_SCHEDULE_ID)
        await handle.update(lambda _: ScheduleUpdate(schedule=schedule))
        return "updated"


async def delete_job_collection_schedule(client: Client) -> None:
    await client.get_schedule_handle(JOB_COLLECTION_SCHEDULE_ID).delete()


def build_apply_intake_schedule(cfg: Settings) -> Schedule:
    """등록될 Schedule 정의. `build_job_collection_schedule`과 같은 이유로 순수 함수다."""
    return Schedule(
        action=ScheduleActionStartWorkflow(
            ApplyIntakeWorkflow.run,
            ApplyIntakeInput(count=cfg.apply_schedule_count),
            id=f"{APPLY_INTAKE_SCHEDULE_ID}-run",
            task_queue=QUEUE_DEFAULT,
        ),
        spec=ScheduleSpec(cron_expressions=[cfg.apply_schedule_cron]),
        # job-collection Schedule 과 같은 이유(§11.2b) — 겹쳐 돌 이유가 없다. dedupe 는
        # ApplyIntakeWorkflow 활동이 REJECT_DUPLICATE 로 이미 하지만, 굳이 겹쳐 돌릴 이유는
        # 없다.
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def ensure_apply_intake_schedule(client: Client, cfg: Settings) -> str:
    """없으면 만들고, 있으면 최신 설정으로 덮어쓴다. 반환값은 "created" | "updated"."""
    schedule = build_apply_intake_schedule(cfg)
    try:
        await client.create_schedule(APPLY_INTAKE_SCHEDULE_ID, schedule)
        return "created"
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(APPLY_INTAKE_SCHEDULE_ID)
        await handle.update(lambda _: ScheduleUpdate(schedule=schedule))
        return "updated"


async def delete_apply_intake_schedule(client: Client) -> None:
    await client.get_schedule_handle(APPLY_INTAKE_SCHEDULE_ID).delete()
