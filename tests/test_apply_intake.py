"""apply_intake — 공고 → `ApplicationWorkflow` 시작.

`start_actionable_applications`: TTL 필터링, 적합도 정렬, count 상한, canonical_key 기반
중복지원 방어(WorkflowAlreadyStartedError → skip), application_state_history 기반 사전
상태 필터(REJECTED 는 후순위, NEEDS_HUMAN/EXPIRED 는 신규 후보와 동등, 그 외 기존 이력은 제외).
`apply_by_url`: 사용자가 직접 지정한 URL 1건 → 플랫폼 확인(wanted 한정) → fetch_job →
canonical_key dedupe → 워크플로우 시작.
실제 Temporal 없이 `_FakeClient.start_workflow` 로 어떤 id/정책으로 불렸는지만 본다.
"""

from datetime import UTC, datetime, timedelta

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from auto_apply.adapters.platform.fixture import FixturePlatformAdapter
from auto_apply.adapters.platform.registry import StaticPlatformRegistry
from auto_apply.apply_intake import JOB_CACHE_TTL, apply_by_url, start_actionable_applications
from auto_apply.config import Settings
from auto_apply.contracts.dto import PersistState
from auto_apply.contracts.job import ApplicabilityVerdict, JobPosting, JobRecord, ScreeningVerdict
from auto_apply.domain.enums import ApplicationState
from auto_apply.domain.job_identity import canonical_key
from tests.conftest import Harness

_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _record(
    *,
    platform: str = "wanted",
    platform_job_id: str,
    company: str,
    title: str,
    fit_score: int = 50,
    collected_at: datetime = _NOW,
    actionable: bool = True,
) -> JobRecord:
    job = JobPosting(
        platform=platform,
        platform_job_id=platform_job_id,
        url=f"https://x/{platform_job_id}",
        company=company,
        title=title,
    )
    return JobRecord(
        job=job,
        screening=ScreeningVerdict(verdict="pass", fit_score=fit_score),
        applicability=ApplicabilityVerdict(
            actionable=actionable, channel="platform_form", apply_url=job.url
        ),
        collected_at=collected_at,
    )


class _FakeClient:
    def __init__(self, *, already_started: set[str] = frozenset()) -> None:
        self.started: list[dict[str, object]] = []
        self._already_started = already_started

    async def start_workflow(self, fn, cmd, *, id, task_queue, id_reuse_policy):
        if id in self._already_started:
            raise WorkflowAlreadyStartedError(workflow_id=id, workflow_type="ApplicationWorkflow")
        self.started.append(
            {"id": id, "task_queue": task_queue, "id_reuse_policy": id_reuse_policy, "cmd": cmd}
        )


def _container(job_rows: dict, *, state_rows: dict | None = None, registry=None) -> object:
    h = Harness(job_rows=job_rows, rows=state_rows or {}, registry=registry)
    return h.container(settings=Settings(storage="memory", llm_provider="stub"))


def _state(application_id: str, state: ApplicationState) -> dict:
    row = PersistState(application_id=application_id, workflow_run_id="run_1", state=state)
    return {application_id: [row]}


async def test_starts_top_n_by_fit_score_within_ttl():
    a = _record(platform_job_id="1", company="A사", title="백엔드", fit_score=60)
    b = _record(platform_job_id="2", company="B사", title="프론트", fit_score=90)
    stale = _record(
        platform_job_id="3",
        company="C사",
        title="데이터",
        fit_score=99,
        collected_at=_NOW - JOB_CACHE_TTL - timedelta(minutes=1),
    )
    job_rows = {(r.job.platform, r.job.platform_job_id): r for r in (a, b, stale)}
    c = _container(job_rows)
    client = _FakeClient()

    result = await start_actionable_applications(1, c, client, now=_NOW)

    # fit_score 90인 B사가 우선이고, TTL 밖인 C사는 애초에 후보에도 안 든다.
    assert result.candidates == 2
    assert result.started == ["B사 - 프론트"]
    assert result.skipped == []
    assert len(client.started) == 1
    assert client.started[0]["id"] == f"application-{canonical_key('B사', '프론트')}"


async def test_already_started_job_is_skipped_not_restarted():
    record = _record(platform_job_id="1", company="A사", title="백엔드")
    job_rows = {(record.job.platform, record.job.platform_job_id): record}
    c = _container(job_rows)
    wf_id = f"application-{canonical_key('A사', '백엔드')}"
    client = _FakeClient(already_started={wf_id})

    result = await start_actionable_applications(3, c, client, now=_NOW)

    assert result.started == []
    assert result.skipped == ["A사 - 백엔드"]
    assert client.started == []


async def test_non_actionable_jobs_are_never_candidates():
    excluded = _record(platform_job_id="1", company="A사", title="백엔드", actionable=False)
    job_rows = {(excluded.job.platform, excluded.job.platform_job_id): excluded}
    c = _container(job_rows)

    result = await start_actionable_applications(3, c, _FakeClient(), now=_NOW)

    assert result.candidates == 0
    assert result.started == []


async def test_uses_allow_duplicate_reuse_policy_so_needs_human_retries_actually_start():
    """RUNNING 중인 동일 id 경합만 막고, COMPLETED(NEEDS_HUMAN 등)로 끝난 뒤엔 재사용을

    막지 않는다는 계약 — REJECT_DUPLICATE는 경합 방지만이 아니라 완료된 워크플로우의 id
    재사용도 영구히 막아서(2026-08-23 라이브로 확인), NEEDS_HUMAN 재시도가 실제로는 항상
    조용히 무시되는 버그였다.
    """
    from temporalio.common import WorkflowIDReusePolicy

    record = _record(platform_job_id="1", company="A사", title="백엔드")
    job_rows = {(record.job.platform, record.job.platform_job_id): record}
    c = _container(job_rows)
    client = _FakeClient()

    await start_actionable_applications(1, c, client, now=_NOW)

    assert client.started[0]["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE


async def test_dry_run_selects_candidates_without_calling_temporal():
    a = _record(platform_job_id="1", company="A사", title="백엔드", fit_score=60)
    b = _record(platform_job_id="2", company="B사", title="프론트", fit_score=90)
    job_rows = {(r.job.platform, r.job.platform_job_id): r for r in (a, b)}
    c = _container(job_rows)
    client = _FakeClient()

    result = await start_actionable_applications(1, c, client, now=_NOW, dry_run=True)

    assert result.dry_run is True
    assert result.started == ["B사 - 프론트"]  # 같은 선정 로직(TTL/정렬/count)이 그대로 적용됨
    assert client.started == []  # start_workflow 자체가 안 불렸다


async def test_dry_run_still_excludes_jobs_with_existing_application_history():
    """dry_run 은 `client.start_workflow`를 안 부르지만, 상태 필터는 `uow.applications`를

    읽기만 하는 조회라 dry_run 여부와 무관하게 적용된다 — 예전엔 이게 없어서 이미 지원한
    공고를 dry_run 미리보기가 "선택될 것"으로 잘못 보여주는 한계가 있었다(모듈 docstring 참고).
    """
    record = _record(platform_job_id="1", company="A사", title="백엔드")
    job_rows = {(record.job.platform, record.job.platform_job_id): record}
    state_rows = _state(canonical_key("A사", "백엔드"), ApplicationState.EXECUTING)
    c = _container(job_rows, state_rows=state_rows)

    result = await start_actionable_applications(3, c, _FakeClient(), now=_NOW, dry_run=True)

    assert result.started == []
    assert result.skipped == ["A사 - 백엔드"]


async def test_job_with_non_retryable_history_is_excluded_from_candidates():
    """진행 중이든 COMPLETED 든, REJECTED/NEEDS_HUMAN 이 아닌 기존 이력이 있으면 이미

    지원 프로세스를 밟은 것으로 보고 후보에서 아예 뺀다(사용자 요청, 2026-08-21).
    """
    old = _record(platform_job_id="1", company="A사", title="백엔드", fit_score=90)
    new = _record(platform_job_id="2", company="B사", title="프론트", fit_score=10)
    job_rows = {(r.job.platform, r.job.platform_job_id): r for r in (old, new)}
    state_rows = _state(canonical_key("A사", "백엔드"), ApplicationState.COMPLETED)
    c = _container(job_rows, state_rows=state_rows)
    client = _FakeClient()

    result = await start_actionable_applications(5, c, client, now=_NOW)

    assert result.started == ["B사 - 프론트"]  # fit_score 낮아도 새 후보라 시작됨
    assert result.skipped == ["A사 - 백엔드"]  # fit_score 더 높아도 이미 COMPLETED 라 제외


async def test_needs_human_jobs_are_treated_as_full_candidates_not_deprioritized():
    """NEEDS_HUMAN 은 REJECTED 와 달리 순위를 밀리지 않고 신규 후보와 완전히 동등하게

    fit_score 순서에 섞인다 — submitted_at 이 항상 None 인 채로만 끝나는 상태라(§apply_intake.py
    docstring) 사람의 의사 표현이 아니기 때문(wanted 363152 공고 실측, 2026-08-22 — 처음엔
    REJECTED 와 같이 후순위로 뒀다가, 사용자가 "후순위로 하지마"로 이 구분을 확정했다).
    """
    stuck = _record(platform_job_id="1", company="A사", title="백엔드", fit_score=99)
    fresh = _record(platform_job_id="2", company="B사", title="프론트", fit_score=10)
    job_rows = {(r.job.platform, r.job.platform_job_id): r for r in (stuck, fresh)}
    state_rows = _state(canonical_key("A사", "백엔드"), ApplicationState.NEEDS_HUMAN)
    c = _container(job_rows, state_rows=state_rows)
    client = _FakeClient()

    # fit_score 가 더 높은 A사(NEEDS_HUMAN)가 신규 후보 B사보다 먼저 뽑힌다 — REJECTED 였다면
    # (아래 test_rejected_jobs_are_deprioritized_not_excluded) B사가 먼저 뽑혔을 것.
    result = await start_actionable_applications(1, c, client, now=_NOW)
    assert result.started == ["A사 - 백엔드"]


async def test_expired_jobs_are_treated_as_full_candidates_not_deprioritized():
    """EXPIRED(승인 대기 72시간 무응답)도 NEEDS_HUMAN 과 같은 취급 — submitted_at 이 항상

    None 인 채로만 끝나는 상태라 사람의 의사 표현이 아니기 때문(2026-08-23, "지원워크플로우가
    expired 되도 재지원되냐"는 질문으로 이 집합에서 빠져 있던 갭이 드러나 추가).
    """
    stuck = _record(platform_job_id="1", company="A사", title="백엔드", fit_score=99)
    fresh = _record(platform_job_id="2", company="B사", title="프론트", fit_score=10)
    job_rows = {(r.job.platform, r.job.platform_job_id): r for r in (stuck, fresh)}
    state_rows = _state(canonical_key("A사", "백엔드"), ApplicationState.EXPIRED)
    c = _container(job_rows, state_rows=state_rows)
    client = _FakeClient()

    # fit_score 가 더 높은 A사(EXPIRED)가 신규 후보 B사보다 먼저 뽑힌다.
    result = await start_actionable_applications(1, c, client, now=_NOW)
    assert result.started == ["A사 - 백엔드"]


async def test_rejected_jobs_are_deprioritized_not_excluded():
    """REJECTED 는 신규 후보 뒤로 순위만 밀린다 — 아예 빼지는 않는다(사람이 다시 볼 여지를

    남긴다는 설계 결정, 2026-08-21).
    """
    rejected = _record(platform_job_id="1", company="A사", title="백엔드", fit_score=99)
    fresh = _record(platform_job_id="2", company="B사", title="프론트", fit_score=10)
    job_rows = {(r.job.platform, r.job.platform_job_id): r for r in (rejected, fresh)}
    state_rows = _state(canonical_key("A사", "백엔드"), ApplicationState.REJECTED)
    c = _container(job_rows, state_rows=state_rows)
    client = _FakeClient()

    # count=1 이면 fit_score 가 훨씬 낮아도 신규 후보(B사)가 REJECTED(A사)보다 먼저 뽑힌다.
    result = await start_actionable_applications(1, c, client, now=_NOW)
    assert result.started == ["B사 - 프론트"]

    # count 를 늘리면 REJECTED 도 순서상 다음 자리에 채워진다 — 완전히 배제되진 않는다.
    client2 = _FakeClient()
    result2 = await start_actionable_applications(2, c, client2, now=_NOW)
    assert result2.started == ["B사 - 프론트", "A사 - 백엔드"]


_WANTED_URL = "https://www.wanted.co.kr/wd/12345"
# FixturePlatformAdapter.fetch_job 이 항상 내주는 값 — apply_by_url 은 job_rows(수집 캐시)를
# 안 거치고 이 platform 어댑터 호출 결과만으로 canonical_key 를 계산한다.
_WANTED_COMPANY = "Fixture Inc."
_WANTED_TITLE = "백엔드 엔지니어"


def _wanted_registry() -> StaticPlatformRegistry:
    return StaticPlatformRegistry(
        [FixturePlatformAdapter(platform="wanted", hosts=("www.wanted.co.kr",))]
    )


async def test_apply_by_url_starts_workflow_for_wanted_link():
    from temporalio.common import WorkflowIDReusePolicy

    c = _container({}, registry=_wanted_registry())
    client = _FakeClient()

    result = await apply_by_url(_WANTED_URL, c, client)

    assert result.outcome == "started"
    assert result.label == f"{_WANTED_COMPANY} - {_WANTED_TITLE}"
    assert len(client.started) == 1
    started = client.started[0]
    assert started["id"] == f"application-{canonical_key(_WANTED_COMPANY, _WANTED_TITLE)}"
    assert started["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE
    # 사람이 붙여넣은 원본 URL을 그대로 워크플로우에 넘긴다(job.url 이 아니라).
    assert started["cmd"].job_url == _WANTED_URL


async def test_apply_by_url_rejects_non_wanted_platform():
    """registry 자체엔 다른 플랫폼도 등록돼 있을 수 있지만, apply_by_url 은 wanted 로 한정한다."""
    other = StaticPlatformRegistry([FixturePlatformAdapter(platform="saramin", hosts=("x.local",))])
    c = _container({}, registry=other)
    client = _FakeClient()

    result = await apply_by_url("https://x.local/job/1", c, client)

    assert result.outcome == "unsupported_platform"
    assert client.started == []


async def test_apply_by_url_rejects_unregistered_domain():
    c = _container({}, registry=_wanted_registry())
    client = _FakeClient()

    result = await apply_by_url("https://example.com/job/1", c, client)

    assert result.outcome == "unsupported_platform"
    assert client.started == []


async def test_apply_by_url_skips_when_already_applied():
    state_rows = _state(canonical_key(_WANTED_COMPANY, _WANTED_TITLE), ApplicationState.EXECUTING)
    c = _container({}, state_rows=state_rows, registry=_wanted_registry())
    client = _FakeClient()

    result = await apply_by_url(_WANTED_URL, c, client)

    assert result.outcome == "duplicate"
    assert result.label == f"{_WANTED_COMPANY} - {_WANTED_TITLE}"
    assert client.started == []


async def test_apply_by_url_allows_retry_after_rejected():
    """REJECTED 이력은 자동 후보 선정과 마찬가지로 재지원을 막지 않는다."""
    state_rows = _state(canonical_key(_WANTED_COMPANY, _WANTED_TITLE), ApplicationState.REJECTED)
    c = _container({}, state_rows=state_rows, registry=_wanted_registry())
    client = _FakeClient()

    result = await apply_by_url(_WANTED_URL, c, client)

    assert result.outcome == "started"
    assert len(client.started) == 1


async def test_apply_by_url_allows_retry_after_needs_human():
    """NEEDS_HUMAN 이력도 REJECTED 와 같은 취급 — submitted_at 이 없는 종결이라 재지원이

    안전하다(wanted 363152 공고 실측, 2026-08-22 — 이전엔 여기서 "이미 지원함"으로 막혔다).
    """
    state_rows = _state(canonical_key(_WANTED_COMPANY, _WANTED_TITLE), ApplicationState.NEEDS_HUMAN)
    c = _container({}, state_rows=state_rows, registry=_wanted_registry())
    client = _FakeClient()

    result = await apply_by_url(_WANTED_URL, c, client)

    assert result.outcome == "started"
    assert len(client.started) == 1


async def test_apply_by_url_allows_retry_after_expired():
    """EXPIRED 이력도 NEEDS_HUMAN/REJECTED 와 같은 취급 — submitted_at 이 없는 종결이라

    재지원이 안전하다(2026-08-23 — 이전엔 이 집합에서 빠져 있어 여기서 "이미 지원함"으로
    막혔다).
    """
    state_rows = _state(canonical_key(_WANTED_COMPANY, _WANTED_TITLE), ApplicationState.EXPIRED)
    c = _container({}, state_rows=state_rows, registry=_wanted_registry())
    client = _FakeClient()

    result = await apply_by_url(_WANTED_URL, c, client)

    assert result.outcome == "started"
    assert len(client.started) == 1


@pytest.mark.temporal
async def test_start_workflow_actually_restarts_after_a_prior_run_completed():
    """ALLOW_DUPLICATE 회귀 테스트 — 위의 `_FakeClient` 기반 테스트들은 "정책 필터를 통과해서

    시작 호출까지 갔다"만 증명하지, 실제 Temporal 이 그 시작을 받아주는지는 fake 로 증명이
    안 된다(2026-08-23 실측: `_FakeClient` 는 항상 받아주므로 REJECT_DUPLICATE 였을 때도
    이 스위트는 전부 통과했었다 — 진짜 서버로 붙여서야 재시도가 늘 조용히 막혀 있었다는 게
    드러났다). 그래서 REJECTED 로 완전히 끝난 진짜 워크플로우에 대고 `_start_workflow` 를
    한 번 더 불러 실제로 재시작되는지, 그리고 RUNNING 중인 동일 id 는 여전히 막히는지를
    실제 서버로 검증한다.
    """
    from temporalio.client import Client
    from temporalio.testing import WorkflowEnvironment

    from auto_apply import apply_intake
    from auto_apply.contracts.dto import RejectSignal
    from auto_apply.temporal_config import DATA_CONVERTER
    from auto_apply.workflows.application import ApplicationWorkflow
    from tests.workflows.test_application import APP_ID, _cmd, _start, _wait_state, _Workers

    async with await WorkflowEnvironment.start_time_skipping(data_converter=DATA_CONVERTER) as env:
        client: Client = env.client
        h = Harness()
        c = h.container(settings=Settings(storage="memory", llm_provider="stub"))
        async with _Workers(client, h):
            handle = await _start(client, _cmd())
            await _wait_state(handle, ApplicationState.AWAITING_APPROVAL)
            await handle.signal(ApplicationWorkflow.reject, RejectSignal())
            await handle.result()  # REJECTED 로 완전히 COMPLETED 됨

            restarted = await apply_intake._start_workflow(
                APP_ID, "https://fixture.local/jobs/1", c, client
            )
            assert restarted, (
                "REJECTED 로 끝난 뒤에도 같은 id 로 재시작돼야 한다"
                "(REJECT_DUPLICATE 로 되돌리면 여기서 실패한다)"
            )

            again = await apply_intake._start_workflow(
                APP_ID, "https://fixture.local/jobs/1", c, client
            )
            assert not again, "RUNNING 중인 동일 id 재시작은 여전히 막혀야 한다"

            handle2 = client.get_workflow_handle(f"application-{APP_ID}")
            await handle2.signal(ApplicationWorkflow.reject, RejectSignal())
            await handle2.result()
