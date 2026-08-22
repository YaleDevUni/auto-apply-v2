"""ApplyIntakeActivities — `ApplyIntakeWorkflow`가 실제로 부르는 activity 구현.

실제 선정/dedupe 로직(TTL·적합도 정렬·상태 필터)은 `auto_apply.apply_intake`가 갖고 있고
`tests/test_apply_intake.py`가 이미 그 로직을 검증한다 — 여기서는 activity 가 그 함수를
`cmd.count`로 올바르게 부르고, 반환값을 workflow-safe `ApplyIntakeResult`(contracts.dto)로
그대로 옮기는지만 본다.
"""

from datetime import UTC, datetime

from auto_apply.activities.apply_intake import ApplyIntakeActivities
from auto_apply.config import Settings
from auto_apply.contracts.dto import ApplyIntakeInput, ApplyIntakeResult
from tests.conftest import Harness
from tests.test_apply_intake import _FakeClient, _record


async def test_delegates_to_start_actionable_applications_with_cmd_count():
    now = datetime.now(UTC)
    record = _record(platform_job_id="1", company="A사", title="백엔드", collected_at=now)
    job_rows = {(record.job.platform, record.job.platform_job_id): record}
    h = Harness(job_rows=job_rows)
    container = h.container(settings=Settings(storage="memory", llm_provider="stub"))
    client = _FakeClient()
    acts = ApplyIntakeActivities(container, client)

    result = await acts.start_actionable_applications(ApplyIntakeInput(count=1))

    assert result == ApplyIntakeResult(started=["A사 - 백엔드"], skipped=[], candidates=1)
    assert len(client.started) == 1


async def test_no_candidates_returns_empty_result():
    h = Harness(job_rows={})
    container = h.container(settings=Settings(storage="memory", llm_provider="stub"))
    acts = ApplyIntakeActivities(container, _FakeClient())

    result = await acts.start_actionable_applications(ApplyIntakeInput(count=3))

    assert result == ApplyIntakeResult(started=[], skipped=[], candidates=0)
