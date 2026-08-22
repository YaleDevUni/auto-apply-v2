"""JobCollectionWorkflow/ApplyIntakeWorkflow 를 주기 실행하는 Temporal Schedule (§11.2b, §11.2f).

등록은 create-or-update로 만들어 몇 번을 실행해도 안전하다(idempotent) — 이미 있으면 최신
설정으로 덮어쓴다. cron/건수/플랫폼 값은 이 모듈이 직접 정하지 않는다 — 호출부(`cli.py`,
`schedule_config.py`)가 DB(`ScheduleConfig`, § apply-schedule)나 `.env` 시드값에서 읽어
그대로 넘긴다. 이 모듈은 그 값을 Temporal Schedule 로 바꾸는 것만 안다.

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

from auto_apply.contracts.dto import ApplyIntakeInput
from auto_apply.contracts.job import CollectJobsInput
from auto_apply.temporal_config import QUEUE_DEFAULT
from auto_apply.workflows.apply_intake import ApplyIntakeWorkflow
from auto_apply.workflows.job_collection import JobCollectionWorkflow

JOB_COLLECTION_SCHEDULE_ID = "job-collection-schedule"
APPLY_INTAKE_SCHEDULE_ID = "apply-intake-schedule"


async def _create_or_update(client: Client, schedule_id: str, schedule: Schedule) -> str:
    """create-or-update 공통 로직. 반환값은 "created" | "updated".

    `ScheduleUpdate(schedule=schedule)`의 `schedule`은 매번 새로 지은 객체라 `state.paused`가
    기본값(False)이다 — 그대로 update 하면 꺼둔 Schedule 이 설정만 바꿔도 조용히 다시 켜진다.
    그래서 업데이트 전 현재 paused 여부를 읽어두고, 꺼져 있었으면 update 뒤에 다시 꺼서
    "시각/건수만 바꾼다"가 "다시 켠다"를 의미하지 않게 한다.
    """
    try:
        await client.create_schedule(schedule_id, schedule)
        return "created"
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(schedule_id)
        was_paused = (await handle.describe()).schedule.state.paused
        await handle.update(lambda _: ScheduleUpdate(schedule=schedule))
        if was_paused:
            await handle.pause(note="설정 변경 후에도 꺼짐 상태 유지")
        return "updated"


def build_job_collection_schedule(cron: str, platforms: list[str]) -> Schedule:
    """등록될 Schedule 정의. 순수 함수라 Temporal 서버 없이도 테스트할 수 있다."""
    return Schedule(
        action=ScheduleActionStartWorkflow(
            JobCollectionWorkflow.run,
            CollectJobsInput(platforms=platforms),
            id=f"{JOB_COLLECTION_SCHEDULE_ID}-run",
            task_queue=QUEUE_DEFAULT,
        ),
        spec=ScheduleSpec(cron_expressions=[cron]),
        # 이전 실행이 안 끝났으면 겹쳐 돌리지 않고 건너뛴다 — collect_platform_jobs 의
        # 재시도 단위가 "플랫폼 전체"라, 겹쳐 돌면 같은 공고를 두 activity가 동시에
        # upsert할 수 있다(§11.2b). JobRepository.upsert가 멱등이라 데이터가 깨지진
        # 않지만, 굳이 중복 실행을 허용할 이유가 없다.
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def ensure_job_collection_schedule(client: Client, cron: str, platforms: list[str]) -> str:
    """없으면 만들고, 있으면 최신 설정으로 덮어쓴다. 반환값은 "created" | "updated"."""
    schedule = build_job_collection_schedule(cron, platforms)
    return await _create_or_update(client, JOB_COLLECTION_SCHEDULE_ID, schedule)


async def delete_job_collection_schedule(client: Client) -> None:
    await client.get_schedule_handle(JOB_COLLECTION_SCHEDULE_ID).delete()


def build_apply_intake_schedule(cron: str, count: int) -> Schedule:
    """등록될 Schedule 정의. `build_job_collection_schedule`과 같은 이유로 순수 함수다."""
    return Schedule(
        action=ScheduleActionStartWorkflow(
            ApplyIntakeWorkflow.run,
            ApplyIntakeInput(count=count),
            id=f"{APPLY_INTAKE_SCHEDULE_ID}-run",
            task_queue=QUEUE_DEFAULT,
        ),
        spec=ScheduleSpec(cron_expressions=[cron]),
        # job-collection Schedule 과 같은 이유(§11.2b) — 겹쳐 돌 이유가 없다. dedupe 는
        # ApplyIntakeWorkflow 활동이 REJECT_DUPLICATE 로 이미 하지만, 굳이 겹쳐 돌릴 이유는
        # 없다.
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )


async def ensure_apply_intake_schedule(client: Client, cron: str, count: int) -> str:
    """없으면 만들고, 있으면 최신 설정으로 덮어쓴다. 반환값은 "created" | "updated"."""
    schedule = build_apply_intake_schedule(cron, count)
    return await _create_or_update(client, APPLY_INTAKE_SCHEDULE_ID, schedule)


async def delete_apply_intake_schedule(client: Client) -> None:
    await client.get_schedule_handle(APPLY_INTAKE_SCHEDULE_ID).delete()
