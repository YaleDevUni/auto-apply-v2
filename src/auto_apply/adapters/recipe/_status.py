"""RecipeSource 구현 2개가 공유하는 status 문구 (§2.4a).

`active()` 가 못 찾았을 때의 사유를 두 어댑터가 각자 쓰면 갈라진다 — 특히 "격리(quarantined)
때문에 막혔다"는 사유는 사람이 텔레그램에서 그대로 읽고 조치(해제/수선 대기)를 판단하는
문구라 표현이 어긋나면 안 된다. contract test 도 이 문구로 두 구현을 같이 검증한다.
"""

from auto_apply.contracts.recipe import AutomationRecipe

QUARANTINE_HINT = "해제하려면 텔레그램에서 unquarantine_recipe 를 요청해라"


def no_live_reason(platform: str, versions: list[AutomationRecipe]) -> str:
    held = [r for r in versions if r.status == "quarantined"]
    if held:
        version = max(r.version for r in held)
        return (
            f"{platform}: recipe v{version} 가 격리(quarantined) 상태다 — "
            f"사람이 '진짜 깨졌다'고 판정해 제출을 멈춘 상태다. {QUARANTINE_HINT}"
        )
    return f"{platform}: active recipe 가 없다"
