"""unquarantine_recipe 도구 (telegram/_agent_tools_recipe.py, §2.4a).

격리는 수선이 승격까지 성공하면 자동으로 풀리지만, 수선이 실패하면 recipe 가 격리된 채로
남아 그 플랫폼 제출이 계속 막힌다 — 사람이 직접 풀 통로가 없으면 텔레그램만 쓰는 운영에서
빠져나올 방법이 없다. 이 도구가 그 통로다.
"""

from auto_apply.bootstrap import Container
from auto_apply.config import Settings
from auto_apply.domain.errors import PolicyViolation
from auto_apply.telegram._agent_tools import TOOLS
from auto_apply.telegram._agent_tools_recipe import _unquarantine_recipe
from tests.conftest import Harness


class _FakeClient:
    """이 도구는 Temporal 을 안 쓴다 — 시그니처만 맞추는 대역."""


def _harness() -> tuple[Harness, Container]:
    h = Harness()
    return h, h.container(settings=Settings(storage="memory", llm_provider="stub"))


async def test_unquarantine_restores_submission_as_supervised_candidate():
    h, c = _harness()
    await h.recipes.quarantine("fixture")

    message = await _unquarantine_recipe({"platform": "fixture"}, c, _FakeClient())  # type: ignore[arg-type]

    assert "격리를 풀었습니다" in message
    assert (await h.recipes.active("fixture")).status == "candidate"


async def test_unquarantine_without_quarantine_reports_instead_of_raising():
    """도구는 채팅 응답 문자열로 실패를 말한다 — 예외가 새면 채팅 턴 전체가 죽는다."""
    _, c = _harness()
    message = await _unquarantine_recipe({"platform": "fixture"}, c, _FakeClient())  # type: ignore[arg-type]
    assert "격리를 풀지 못했습니다" in message


async def test_unquarantine_requires_platform():
    _, c = _harness()
    assert "platform" in await _unquarantine_recipe({}, c, _FakeClient())  # type: ignore[arg-type]


async def test_quarantined_recipe_is_not_runnable_until_unquarantined():
    """도구를 부르기 전까지는 실행 조회 자체가 막혀 있어야 한다 (§2.4a 의 "제출 중단")."""
    h, c = _harness()
    await h.recipes.quarantine("fixture")
    try:
        await h.recipes.active("fixture")
    except PolicyViolation as e:
        assert "격리" in str(e)
    else:
        raise AssertionError("격리된 recipe 가 그대로 실행 대상이 됐다")

    await _unquarantine_recipe({"platform": "fixture"}, c, _FakeClient())  # type: ignore[arg-type]
    assert await h.recipes.active("fixture")


def test_unquarantine_tool_is_wired_into_chat_agent():
    assert "unquarantine_recipe" in TOOLS
