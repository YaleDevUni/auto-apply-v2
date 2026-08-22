"""공고 → `ApplicationWorkflow` 시작. 텔레그램 채팅 에이전트의 `start_applications`/

`apply_by_url` 도구(`telegram/agent.py`)가 쓰는 진입점이다. `start_actionable_applications`는
"제출해줘 N건" 요청을 사람이 매번 `POST /applications`/`auto-apply start`로 공고 URL을 손으로
골라 넣는 대신 자동화하려는 설계(2026-08-21 세션에서 사용자와 확정)이고, `apply_by_url`은
반대로 사람이 URL을 직접 지정하는("이 링크 지원해줘") 경로다 — 같은 "워크플로우 시작"을
공유해서 여기 같이 둔다.

`cli.py`/`watchdog.py`처럼 Temporal Client SDK를 직접 쓰는 운영 진입점이다(§11.3 대상 아님,
workflow 파일이 아니다) — 새 port를 만들지 않는다. `uow.jobs.actionable()`은
`JobCollectionWorkflow`(Schedule로 주기 실행)가 채워둔 캐시를 읽기만 한다 — 요청마다 라이브
재수집을 하면 응답이 느려지고 플랫폼 rate limit도 갉아먹는다. `JOB_CACHE_TTL`을 넘긴 오래된
캐시는 무시한다(사용자 결정) — 마감 지난 공고를 다시 지원 시도하는 걸 막는 안전판이기도 하다.

`domain.job_identity.canonical_key`(회사+직무 기반 결정론 해시)를 그대로 `application_id`로
쓰는 게 중복지원 방어선이다(그 모듈 docstring 참고). 이 id로 `uow.applications.latest_states`를
찔러 이미 이력이 있는 공고를 후보 선정 단계에서 미리 갈라낸다(`_partition_by_state`,
2026-08-21 세션 사용자 요청 — "거절된 건 후순위로, 이미 지원한 건 미리 걸러야" 하지 않냐는
지적으로 추가) — REJECTED/NEEDS_HUMAN 은 완전히 빼지는 않고 신규 후보 뒤로 순위만 미룬다
(`_RETRYABLE_STATES`). REJECTED 는 사람이 그 공고 자체를 다시 안 보고 싶다는 뜻은 아닐 수
있어서고, NEEDS_HUMAN 은 다른 이유다 — 실제 워크플로우 종료 경로(`workflows/application.py`,
`workflows/_execution.py`)를 보면 NEEDS_HUMAN 으로 끝나는 모든 경로가 submitted_at 을
None 으로만 남긴다(실행 activity 실패든 verify 미확인이든, 실제 제출이 안 됐다고 확인됐을
때만 이 상태로 떨어진다 — §5 "부분 제출 위험 방어"). 즉 NEEDS_HUMAN 은 "제출은 안 됐는데
사람이 봐야 하는 상태"이지 "이미 지원 프로세스를 끝낸 상태"가 아니라서, 원인(예: recipe
selector 변경, claude CLI 한도초과로 자동 수선 실패)이 나중에 해소되면 재시도가 안전하다
(2026-08-22 세션 실측 — wanted 363152 공고, submitted_at=null 확인 후 재지원 시도가
"이미 지원함"으로 막혀서 발견). 두 상태를 제외한 나머지(진행 중이든 COMPLETED 등 실제
종결 상태든)는 여전히 "이미 지원 프로세스를 밟은 공고"로 보고 후보에서 아예 뺀다.

Temporal 기본 `WorkflowIDReusePolicy.ALLOW_DUPLICATE`는 이전 실행이 COMPLETED로 끝난 뒤엔
같은 id로 새로 시작하는 걸 막지 않으므로, 실제 시작 호출에서는 그와 별개로 명시적으로
REJECT_DUPLICATE를 준다 — 위 사전 필터와 시작 호출 사이의 경합(같은 공고에 거의 동시에 두
요청이 들어오는 경우)을 잡는 마지막 안전판이다(`workflows/_repair.py`의 child workflow
dedupe와 같은 패턴). `WorkflowAlreadyStartedError`로 걸러 조용히 건너뛴다.

실제 최종 제출은 여전히 사람이 텔레그램 승인 버튼을 눌러야 일어난다 — 이 함수는 워크플로우를
"시작"만 할 뿐이고, CLAUDE.md 절대규칙 4는 `ApplicationWorkflow` 안의 승인 대기가 그대로 지킨다.

`dry_run`은 그 승인 대기(§9.5 `DRY_RUN_ONLY`, 실행 단계의 제출 여부)와는 다른 레벨이다 —
여기서는 "선정 로직(TTL/정렬/상태필터/count)이 뭘 골랐을지 Temporal을 전혀 건드리지 않고
미리 본다"는 뜻이라, `client.start_workflow` 자체를 호출하지 않는다. 상태 필터는
`uow.applications`를 읽기만 할 뿐 워크플로우를 시작하지 않으므로 dry_run 여부와 무관하게
항상 적용된다 — 예전엔 dry_run 경로가 이 필터를 못 태워서 이미 지원한 공고를 "선택될 것"으로
잘못 보여주는 한계가 있었는데, 사전 필터를 앞으로 옮기면서 해소됐다. 테스트/운영 확인용으로
2026-08-21 추가(사용자 요청).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from auto_apply.bootstrap import Container
from auto_apply.contracts.dto import StartApplication
from auto_apply.contracts.job import JobRecord
from auto_apply.domain.enums import ApplicationState
from auto_apply.domain.errors import PolicyViolation
from auto_apply.domain.job_identity import canonical_key
from auto_apply.temporal_config import QUEUE_DEFAULT
from auto_apply.workflows.application import ApplicationWorkflow

JOB_CACHE_TTL = timedelta(hours=24)

# apply_by_url 도구는 wanted 로만 한정한다(2026-08-21 사용자 요청) — saramin 은 recipe/실행이
# 아직 라이브 검증이 안 끝나서(자소서 문항 있는 공고 미검증, 메모리 saramin-recipe-progress.md)
# 임의 링크로 사람 개입 없이 실행을 트리거하기엔 이르다는 판단. registry 자체엔 이미
# saramin 도 등록돼 있어(bootstrap._build_registry) for_url 만으로는 못 막는다.
_APPLY_BY_URL_PLATFORMS = frozenset({"wanted"})

# 재시도를 막지 않는(=완전히 배제하지 않는) 기존 이력 상태. REJECTED 는 사람의 명시적
# 거절이라 후순위로만 미루고, NEEDS_HUMAN 은 submitted_at 이 항상 None 인 채로만 끝나는
# 상태(위 docstring)라 같은 취급이다 — 둘 다 "제출까지 가지 않은 이력"이라는 공통점.
_RETRYABLE_STATES = frozenset({ApplicationState.REJECTED, ApplicationState.NEEDS_HUMAN})


@dataclass(frozen=True, slots=True)
class ApplyIntakeResult:
    started: list[str] = field(default_factory=list)  # "회사 - 직무" 라벨. dry_run=True 면
    # 실제로 시작한 게 아니라 "시작했을" 후보 — 필드 이름은 그대로 두고 `dry_run` 플래그로
    # 의미를 구분한다(호출부가 이 플래그로 문구를 갈아 끼운다).
    skipped: list[str] = field(default_factory=list)  # 이미 진행 중이거나 끝난 것(dedupe) —
    # 사전 상태 필터(`_RETRYABLE_STATES` 제외 기존 이력)와, 그걸 빠져나간 뒤 시작 시점 경합으로
    # 걸리는 WorkflowAlreadyStartedError 둘 다 여기로 모인다.
    candidates: int = 0  # TTL 창 안에서 발견된 actionable 후보 총 수(상태 필터 전, count와 무관)
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class ApplyByUrlResult:
    outcome: Literal["started", "duplicate", "unsupported_platform", "not_found"]
    label: str | None = None  # "회사 - 직무" — started/duplicate 일 때만 채워짐
    detail: str | None = None  # unsupported_platform/not_found 사유(사람이 읽을 문구)


def _fresh_actionable(records: list[JobRecord], *, now: datetime) -> list[JobRecord]:
    cutoff = now - JOB_CACHE_TTL
    fresh = [r for r in records if r.collected_at >= cutoff]
    # 적합도(fit_score) 높은 것부터 — 개수 상한(count)에 걸릴 때 더 맞는 공고를 우선한다.
    return sorted(fresh, key=lambda r: r.screening.fit_score, reverse=True)


def _partition_by_state(
    keyed: list[tuple[str, JobRecord]],
    states: dict[str, ApplicationState],
    skipped: list[str],
) -> list[tuple[str, JobRecord]]:
    """이력이 없거나 `_RETRYABLE_STATES`(REJECTED/NEEDS_HUMAN)인 것만 후보로 남긴다 — 그

    둘은 뒤로, 나머지 기존 이력은 아예 뺀다(`skipped`에 라벨을 적립). fit_score 순서(호출부가
    `keyed`를 이미 정렬해 넘긴다)는 각 그룹 안에서 그대로 유지된다 — 안정 정렬로 새 후보
    다음에 재시도 후보를 이어붙인다.
    """
    new: list[tuple[str, JobRecord]] = []
    retryable: list[tuple[str, JobRecord]] = []
    for application_id, record in keyed:
        state = states.get(application_id)
        if state is None:
            new.append((application_id, record))
        elif state in _RETRYABLE_STATES:
            retryable.append((application_id, record))
        else:
            job = record.job
            skipped.append(f"{job.company} - {job.title}")
    return new + retryable


async def _start_workflow(application_id: str, job_url: str, c: Container, client: Client) -> bool:
    """워크플로우 시작 1건(REJECT_DUPLICATE dedupe 포함) — `start_actionable_applications`의

    루프와 `apply_by_url` 이 공유한다. 이미 시작돼 있으면(경합 등) False, 새로 시작했으면 True.
    """
    try:
        await client.start_workflow(
            ApplicationWorkflow.run,
            StartApplication(
                application_id=application_id,
                user_id=c.settings.default_user_id,
                job_url=job_url,
                approval_timeout_hours=c.settings.approval_timeout_hours,
                dry_run_only=c.settings.dry_run_only,
                max_revisions=c.settings.max_revisions,
                max_guide_revisions=c.settings.max_guide_revisions,
            ),
            id=f"application-{application_id}",
            task_queue=QUEUE_DEFAULT,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
        return True
    except WorkflowAlreadyStartedError:
        return False


async def apply_by_url(url: str, c: Container, client: Client) -> ApplyByUrlResult:
    """공고 링크 1건 → `ApplicationWorkflow` 시작. 텔레그램 채팅 에이전트의 `apply_by_url`

    도구(`telegram/_agent_tools.py`)가 쓰는 진입점 — "이 링크 지원해줘"처럼 사용자가 공고를
    직접 지정하는 경로다(2026-08-21, "wanted 링크 보내면 지원 프로세스 도는 기능 있냐"는
    질문에서 시작해 신설). `start_actionable_applications`(자동 후보 선정)와 달리 적합도
    랭킹/count/TTL 캐시가 없다 — 사용자가 이미 골랐으니 다시 스크리닝하지 않는다. `job.url`이
    아니라 호출자가 준 `url`을 그대로 워크플로우에 넘긴다 — 사람이 붙여넣은 원본 링크를
    보존해야 나중에 워크플로우 상태를 봤을 때 "이 링크로 시작했다"가 그대로 남는다(리다이렉트
    등으로 값이 갈리면 `fetch_job`이 잡아낸 `job.company`/`job.title`과의 canonical_key 계산엔
    영향 없다 — 그건 API 응답에서 나온다).
    """
    try:
        adapter = c.registry.for_url(url)
    except PolicyViolation as e:
        return ApplyByUrlResult(outcome="unsupported_platform", detail=str(e))
    if adapter.platform not in _APPLY_BY_URL_PLATFORMS:
        return ApplyByUrlResult(
            outcome="unsupported_platform",
            detail=f"'{adapter.platform}' 링크는 아직 지원하지 않습니다(원티드만 가능).",
        )

    try:
        job = await adapter.fetch_job(url)
    except PolicyViolation as e:
        return ApplyByUrlResult(outcome="not_found", detail=str(e))

    application_id = canonical_key(job.company, job.title)
    if not application_id:
        return ApplyByUrlResult(
            outcome="not_found", detail="공고에서 회사명/직무를 확인할 수 없습니다."
        )
    label = f"{job.company} - {job.title}"

    async with c.uow() as uow:
        states = await uow.applications.latest_states([application_id])
    state = states.get(application_id)
    if state is not None and state not in _RETRYABLE_STATES:
        return ApplyByUrlResult(outcome="duplicate", label=label)

    if await _start_workflow(application_id, url, c, client):
        return ApplyByUrlResult(outcome="started", label=label)
    return ApplyByUrlResult(outcome="duplicate", label=label)


async def start_actionable_applications(
    count: int,
    c: Container,
    client: Client,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> ApplyIntakeResult:
    """TTL 안에서 수집된 actionable 공고 중 이미 지원 이력이 없는(또는 `_RETRYABLE_STATES` 뿐인) 것

    상위 `count`건에 대해 `ApplicationWorkflow`를 새로 시작한다. `now`를 인자로 받는 건
    결정성 때문이 아니라(이 함수는 workflow 코드가 아니다) 테스트에서 TTL 경계를 고정하기
    위해서다. `dry_run=True`면 후보 선정까지만 하고 `client.start_workflow`는 부르지 않는다
    (모듈 docstring의 dry_run 문단 참고).
    """
    now = now or datetime.now(UTC)
    async with c.uow() as uow:
        records = await uow.jobs.actionable()
    fresh = _fresh_actionable(records, now=now)
    result = ApplyIntakeResult(candidates=len(fresh), dry_run=dry_run)

    keyed: list[tuple[str, JobRecord]] = []
    for record in fresh:
        job = record.job
        application_id = canonical_key(job.company, job.title)
        if application_id:  # 회사명/직무가 둘 다 비어 정규화 결과가 빈 공고 — 스킵
            keyed.append((application_id, record))

    async with c.uow() as uow:
        states = await uow.applications.latest_states([aid for aid, _ in keyed])
    ordered = _partition_by_state(keyed, states, result.skipped)

    for application_id, record in ordered:
        if len(result.started) >= count:
            break
        job = record.job
        label = f"{job.company} - {job.title}"
        if dry_run:
            result.started.append(label)
            continue
        if await _start_workflow(application_id, job.url, c, client):
            result.started.append(label)
        else:
            result.skipped.append(label)
    return result
