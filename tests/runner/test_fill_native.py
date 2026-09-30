"""fill run e2e (native) — Scripted 런타임 + 실제 Chrome + 짐 사이트 → AWAITING_APPROVAL, 제출 0건.

짐 서버가 받은 요청으로 확인한다(§A4 테스트 짐). multi_step_form 은 type=submit "다음"·"저장 후
계속" 으로 단계를 넘기고(D17 step 창) 마지막 "제출하기" 가 최종 제출이다.
"""

import pytest

from auto_apply.adapters.agent.scripted import ScriptedCall
from auto_apply.adapters.browser.playwright_guarded import PlaywrightGuardedPageDriver
from auto_apply.domain.enums import ApplicationState as S
from auto_apply.domain.enums import RunStatus
from tests.browsers import real_host
from tests.gym.fixtures import gym
from tests.runner.fill_kit import PROFILE, FillRig, ref, scripted, step

pytestmark = pytest.mark.native
__all__ = ["gym"]
SITE = "multi_step_form"


@pytest.fixture
async def rig(tmp_path):
    rig = FillRig(tmp_path)
    host = real_host(tmp_path / "data" / "chrome-profile")
    rig.host, rig.driver = host, PlaywrightGuardedPageDriver(host)  # type: ignore[assignment]
    yield rig
    await host.close()


async def test_scripted_fill_run_on_gym_reaches_approval_without_submitting(rig, gym):
    user = {"kind": "user"}
    runtime = scripted(
        [
            ScriptedCall("navigate", {"url": gym.entry_url(SITE)}),
            ScriptedCall("snapshot"),
            step("fill", ref=ref("이름"), value="홍길동", source=PROFILE),
            step("click", ref=ref("다음")),
            ScriptedCall("snapshot"),
            step("fill", ref=ref("경력 요약"), value="백엔드 3년", source=user),
            step("click", ref=ref("저장 후 계속")),
            ScriptedCall("snapshot"),
            step("ready_for_review", submit_ref=ref("제출하기")),
        ]
    )
    app, job = await rig.queued()
    await rig.handler(runtime)(job)

    assert all(r.ok for r in runtime.replies), [r.content for r in runtime.replies if not r.ok]
    assert await rig.state(app) is S.AWAITING_APPROVAL
    assert gym.final_submissions(SITE) == []  # 제출 0건
    assert len(gym.intermediate_requests(SITE)) == 2  # 단계 이동은 통과했다
    run = await rig.run_record(app)
    assert run.status is RunStatus.DONE
    review = await rig.artifacts.load_review(run.run_id)
    assert review is not None and review.step == 3
    entries = review.fill_log.entries
    assert [(e.step, e.value) for e in entries] == [(1, "홍길동"), (2, "백엔드 3년")]
