"""application_id 만으로 재시도하는 도구 — `telegram/_agent_tools.py`가 `TOOLS`에 병합한다.

`apply_by_url`은 URL을 알아야 하는데, `list_applications`가 보여주는 건 NEEDS_HUMAN 등으로
끝난 지원 건의 application_id(해시)뿐이라 원 공고 링크를 다시 찾을 방법이 없었다(2026-08-23,
claude CLI 한도초과로 NEEDS_HUMAN 떨어진 지원 건을 한도 해결 뒤 재시도하려던 상황에서 드러난
갭). `apply_intake.retry_application`이 job 캐시에서 URL을 역으로 찾아 재시작한다 — 그 함수
docstring 참고.
"""

from collections.abc import Awaitable, Callable

from temporalio.client import Client

from auto_apply.apply_intake import retry_application
from auto_apply.bootstrap import Container

ToolHandler = Callable[[dict[str, str], Container, Client], Awaitable[str]]


async def _retry_application(args: dict[str, str], c: Container, client: Client) -> str:
    application_id = args.get("application_id", "").strip()
    if not application_id:
        return "application_id가 필요합니다."
    result = await retry_application(application_id, c, client)
    match result.outcome:
        case "not_found":
            return result.detail or f"{application_id}: 재시도할 수 없습니다."
        case "duplicate":
            return f"{result.label}: 이미 지원 이력이 있거나 진행 중입니다."
        case "started":
            return (
                f"{result.label}: 지원 워크플로우를 다시 시작했습니다 (제출 전 승인 요청이 옵니다)."
            )
    raise AssertionError(f"unreachable outcome: {result.outcome}")


RETRY_TOOLS: dict[str, tuple[str, tuple[str, ...], ToolHandler]] = {
    "retry_application": (
        "application_id(해시) 하나만으로 실패한 지원 건(NEEDS_HUMAN/EXPIRED/REJECTED)을"
        " 다시 시도한다 — URL을 몰라도 된다(list_applications/get_application 으로 본"
        " application_id 를 그대로 쓴다). '이거 다시 시도해줘', '한도 풀렸으니 재시도해줘'"
        " 처럼 실패 원인이 해결된 뒤 특정 지원 건을 다시 돌릴 때 쓴다 — apply_by_url 과 달리"
        " 원 공고 링크를 사람이 몰라도 job 캐시에서 찾아 쓴다(캐시에 없으면 apply_by_url로"
        " 안내한다). 실제 제출은 여전히 사람이 텔레그램 승인 버튼을 눌러야 일어난다.",
        ("application_id",),
        _retry_application,
    ),
}
