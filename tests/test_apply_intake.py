"""apply_intake.start_actionable_applications — TTL 필터링, 적합도 정렬, count 상한,

canonical_key 기반 중복지원 방어(WorkflowAlreadyStartedError → skip).
실제 Temporal 없이 `_FakeClient.start_workflow` 로 어떤 id/정책으로 불렸는지만 본다.
"""

from datetime import UTC, datetime, timedelta

from temporalio.exceptions import WorkflowAlreadyStartedError

from auto_apply.apply_intake import JOB_CACHE_TTL, start_actionable_applications
from auto_apply.config import Settings
from auto_apply.contracts.job import ApplicabilityVerdict, JobPosting, JobRecord, ScreeningVerdict
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


def _container(job_rows: dict) -> object:
    h = Harness(job_rows=job_rows)
    return h.container(settings=Settings(storage="memory", llm_provider="stub"))


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


async def test_uses_reject_duplicate_reuse_policy_as_the_dedup_guard():
    """canonical_key 를 다시 써도 Temporal 기본 정책(ALLOW_DUPLICATE)에 기대지 않는다는 계약."""
    from temporalio.common import WorkflowIDReusePolicy

    record = _record(platform_job_id="1", company="A사", title="백엔드")
    job_rows = {(record.job.platform, record.job.platform_job_id): record}
    c = _container(job_rows)
    client = _FakeClient()

    await start_actionable_applications(1, c, client, now=_NOW)

    assert client.started[0]["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE


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


async def test_dry_run_does_not_dedupe_against_already_started_workflows():
    """dry_run 은 Temporal 을 안 건드리므로 WorkflowAlreadyStartedError 로 거르는 dedupe가

    이 경로에선 작동하지 않는다 — 후보 선정만 보여준다는 게 계약이다(모듈 docstring 참고).
    """
    record = _record(platform_job_id="1", company="A사", title="백엔드")
    job_rows = {(record.job.platform, record.job.platform_job_id): record}
    c = _container(job_rows)
    wf_id = f"application-{canonical_key('A사', '백엔드')}"
    client = _FakeClient(already_started={wf_id})

    result = await start_actionable_applications(3, c, client, now=_NOW, dry_run=True)

    assert result.started == ["A사 - 백엔드"]
    assert result.skipped == []
