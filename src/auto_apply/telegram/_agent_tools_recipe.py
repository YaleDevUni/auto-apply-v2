"""Recipe 격리 해제 도구 (§2.4a). `_agent_tools.py` 가 병합한다.

격리(quarantined)는 사람이 "이 recipe 진짜 깨졌다"를 확정했을 때만 걸리고, 수선이 승격까지
성공하면 자동으로 풀린다(`promote()` 가 옛 버전을 deprecated 로 내린다). 문제는 **수선이
실패했을 때**다 — 그때는 recipe 가 격리된 채 남아 그 플랫폼 제출이 계속 막힌다. 그게 의도한
동작이지만(깨진 recipe 로 계속 제출을 시도하지 않는다), 사람이 직접 recipe 를 고쳤거나
"사실 안 깨졌더라"로 판단을 바꿨을 때 풀 통로가 있어야 한다 — 텔레그램이 이 프로젝트의
운영 콘솔이므로(§6) 여기 둔다.

해제는 active 가 아니라 **candidate** 로 되돌린다(ports/recipe_source.py) — 한 번 깨졌다고
판정된 recipe 는 다음 실행이 SUPERVISED 로 돌아 사람이 submit 직전을 눈으로 본다.
"""

from collections.abc import Awaitable, Callable

from temporalio.client import Client

from auto_apply.bootstrap import Container
from auto_apply.domain.errors import AutoApplyError

ToolHandler = Callable[[dict[str, str], Container, Client], Awaitable[str]]


async def _unquarantine_recipe(args: dict[str, str], c: Container, _client: Client) -> str:
    platform = args.get("platform", "").strip()
    if not platform:
        return "platform이 필요합니다."
    try:
        restored = await c.recipes.unquarantine(platform)
    except AutoApplyError as e:
        return f"{platform}: 격리를 풀지 못했습니다 — {e}"
    return (
        f"{platform} recipe v{restored.version} 격리를 풀었습니다 (status=candidate). "
        "다음 지원은 SUPERVISED 로 돌아 제출 직전에 스크린샷 승인을 요청합니다."
    )


RECIPE_TOOLS: dict[str, tuple[str, tuple[str, ...], ToolHandler]] = {
    "unquarantine_recipe": (
        "격리(quarantined)된 recipe 의 격리를 풀어 그 플랫폼 지원을 다시 가능하게 만든다."
        " '원티드 recipe 격리 해제', '다시 지원되게 해줘', 'recipe 풀어줘' 같은 요청에 쓴다."
        " 해제된 recipe 는 candidate 상태로 돌아와 다음 실행이 SUPERVISED(제출 직전 스크린샷"
        " 승인)로 돈다.",
        ("platform",),
        _unquarantine_recipe,
    ),
}
