"""actionable 공고 → `ApplicationWorkflow` 시작. 텔레그램 채팅 에이전트의 `start_applications`

도구(`telegram/agent.py`)가 쓰는 진입점이다 — "제출해줘 N건" 요청을 사람이 매번
`POST /applications`/`auto-apply start`로 공고 URL을 손으로 골라 넣는 대신 자동화하려는
설계(2026-08-21 세션에서 사용자와 확정).

`cli.py`/`watchdog.py`처럼 Temporal Client SDK를 직접 쓰는 운영 진입점이다(§11.3 대상 아님,
workflow 파일이 아니다) — 새 port를 만들지 않는다. `uow.jobs.actionable()`은
`JobCollectionWorkflow`(Schedule로 주기 실행)가 채워둔 캐시를 읽기만 한다 — 요청마다 라이브
재수집을 하면 응답이 느려지고 플랫폼 rate limit도 갉아먹는다. `JOB_CACHE_TTL`을 넘긴 오래된
캐시는 무시한다(사용자 결정) — 마감 지난 공고를 다시 지원 시도하는 걸 막는 안전판이기도 하다.

`domain.job_identity.canonical_key`(회사+직무 기반 결정론 해시)를 그대로 `application_id`로
쓰는 게 중복지원 방어선이다(그 모듈 docstring 참고). Temporal 기본
`WorkflowIDReusePolicy.ALLOW_DUPLICATE`는 이전 실행이 COMPLETED로 끝난 뒤엔 같은 id로 새로
시작하는 걸 막지 않으므로, 이 호출에서만 명시적으로 REJECT_DUPLICATE를 줘서 "이 공고로 지원을
이미 한 번이라도 시작했으면 절대 다시 시작하지 않는다"를 강제한다 — `WorkflowAlreadyStartedError`
로 걸러 조용히 건너뛴다(`workflows/_repair.py`의 child workflow dedupe와 같은 패턴).

실제 최종 제출은 여전히 사람이 텔레그램 승인 버튼을 눌러야 일어난다 — 이 함수는 워크플로우를
"시작"만 할 뿐이고, CLAUDE.md 절대규칙 4는 `ApplicationWorkflow` 안의 승인 대기가 그대로 지킨다.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from auto_apply.bootstrap import Container
from auto_apply.contracts.dto import StartApplication
from auto_apply.contracts.job import JobRecord
from auto_apply.domain.job_identity import canonical_key
from auto_apply.temporal_config import QUEUE_DEFAULT
from auto_apply.workflows.application import ApplicationWorkflow

JOB_CACHE_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ApplyIntakeResult:
    started: list[str] = field(default_factory=list)  # "회사 - 직무" 라벨, 새로 시작한 것
    skipped: list[str] = field(default_factory=list)  # 이미 진행 중이거나 끝난 것(dedupe)
    candidates: int = 0  # TTL 창 안에서 발견된 actionable 후보 총 수(count와 무관)


def _fresh_actionable(records: list[JobRecord], *, now: datetime) -> list[JobRecord]:
    cutoff = now - JOB_CACHE_TTL
    fresh = [r for r in records if r.collected_at >= cutoff]
    # 적합도(fit_score) 높은 것부터 — 개수 상한(count)에 걸릴 때 더 맞는 공고를 우선한다.
    return sorted(fresh, key=lambda r: r.screening.fit_score, reverse=True)


async def start_actionable_applications(
    count: int, c: Container, client: Client, *, now: datetime | None = None
) -> ApplyIntakeResult:
    """TTL 안에서 수집된 actionable 공고 상위 `count`건에 대해 `ApplicationWorkflow`를 새로

    시작한다. `now`를 인자로 받는 건 결정성 때문이 아니라(이 함수는 workflow 코드가 아니다)
    테스트에서 TTL 경계를 고정하기 위해서다.
    """
    now = now or datetime.now(UTC)
    async with c.uow() as uow:
        records = await uow.jobs.actionable()
    fresh = _fresh_actionable(records, now=now)
    result = ApplyIntakeResult(candidates=len(fresh))
    for record in fresh:
        if len(result.started) >= count:
            break
        job = record.job
        application_id = canonical_key(job.company, job.title)
        if not application_id:  # 회사명/직무가 둘 다 비어 정규화 결과가 빈 공고 — 스킵
            continue
        label = f"{job.company} - {job.title}"
        try:
            await client.start_workflow(
                ApplicationWorkflow.run,
                StartApplication(
                    application_id=application_id,
                    user_id=c.settings.default_user_id,
                    job_url=job.url,
                    approval_timeout_hours=c.settings.approval_timeout_hours,
                    dry_run_only=c.settings.dry_run_only,
                    max_revisions=c.settings.max_revisions,
                    max_guide_revisions=c.settings.max_guide_revisions,
                ),
                id=f"application-{application_id}",
                task_queue=QUEUE_DEFAULT,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
            result.started.append(label)
        except WorkflowAlreadyStartedError:
            result.skipped.append(label)
    return result
