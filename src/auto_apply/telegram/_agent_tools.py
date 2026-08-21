"""`telegram/agent.py`의 ReAct 루프가 부르는 도구 레지스트리 — 실제 실행(읽기/버튼 재전송/

워크플로우 시작)은 전부 여기 있다. `agent.py`가 이 파일을 계속 늘리면 "루프 오케스트레이션"과
"도구 구현"이라는 다른 책임이 한 파일에 섞여서 분리했다(§ CLAUDE.md "한 파일 = 한 책임").

행동성 도구 중 `resend_pending_decision`은 워크플로우를 직접 mutate 하지 않는다 — 기존 승인
버튼의 nonce(`ApplicationWorkflow.pending_decision` query)를 그대로 실어 원래 버튼과 동일하게
동작하는 메시지를 다시 보낼 뿐이다. `start_applications`/`apply_by_url`은 실제로
`ApplicationWorkflow`를 새로 "시작"한다(전자는 2026-08-21 "상주 에이전트가 알아서 몇 건
지원해줘" 요청, 후자는 같은 날 "링크 보내면 지원 프로세스 도는 기능 있냐"는 질문에서 이어진
요청으로 설계·구현, `apply_intake.py`) — 하지만 그 워크플로우 자체가 실제 제출 전 텔레그램
승인을 기다리게 돼 있어서 최종 submit은 여전히 사람이 버튼을 누르는 순간에만 일어난다. 즉
CLAUDE.md 절대규칙 4("되돌릴 수 없는 행위는 사람 승인 뒤에서만")를 자연어 오인식 경로로
우회하지 않는다는 불변식은 세 도구 모두 지킨다 — "워크플로우를 안 건드린다"가 아니라 "제출은
못 건드린다"가 진짜 불변식이다. `apply_by_url`은 추가로 플랫폼을 wanted 로만 한정한다
(`apply_intake._APPLY_BY_URL_PLATFORMS` 참고 — saramin 은 아직 임의 링크를 사람 개입 없이
실행 트리거하기엔 라이브 검증이 부족하다는 판단).
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from temporalio.client import Client
from temporalio.service import RPCError

from auto_apply.apply_intake import apply_by_url, start_actionable_applications
from auto_apply.bootstrap import Container
from auto_apply.domain.chat_agent import ToolCatalogEntry
from auto_apply.workflows.application import ApplicationWorkflow

ToolHandler = Callable[[dict[str, str], Container, Client], Awaitable[str]]

_MIN_LIMIT = 1
_MAX_LIMIT = 20
_DEFAULT_LIMIT = 5

# start_applications 전용 상한 — 실제로 워크플로우를 시작시키는 도구라 조회 도구(_MAX_LIMIT)
# 보다 훨씬 보수적으로 잡는다. 한 대화 턴에서 폭주하듯 시작되는 걸 막는 안전판.
_MIN_APPLY_COUNT = 1
_MAX_APPLY_COUNT = 10
_DEFAULT_APPLY_COUNT = 3


@runtime_checkable
class _ResendableNotifier(Protocol):
    """`resend_pending_decision` 도구가 쓰는 구조적 계약 — telegram/bridge.py 의

    `_RevisableNotifier`와 같은 이유로 구체 타입(`TelegramNotifier`)을 이름으로 가리키지 않는다.
    """

    async def resend_decision(self, application_id: str, nonce: str) -> None: ...


def _parse_limit(raw: str) -> int:
    try:
        limit = int(raw) if raw else _DEFAULT_LIMIT
    except ValueError:
        limit = _DEFAULT_LIMIT
    return max(_MIN_LIMIT, min(_MAX_LIMIT, limit))


def _parse_apply_count(raw: str) -> int:
    try:
        count = int(raw) if raw else _DEFAULT_APPLY_COUNT
    except ValueError:
        count = _DEFAULT_APPLY_COUNT
    return max(_MIN_APPLY_COUNT, min(_MAX_APPLY_COUNT, count))


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"true", "1", "yes", "y", "on"}


async def _list_applications(args: dict[str, str], c: Container, _client: Client) -> str:
    limit = _parse_limit(args.get("limit", ""))
    async with c.uow() as uow:
        summaries = await uow.applications.list_recent(limit=limit)
    if not summaries:
        return "지원 건이 없습니다."
    lines = [
        f"{s.application_id}: {s.state}" + (f" ({s.reason})" if s.reason else "") for s in summaries
    ]
    return "\n".join(lines)


async def _get_application(args: dict[str, str], c: Container, _client: Client) -> str:
    application_id = args.get("application_id", "").strip()
    if not application_id:
        return "application_id가 필요합니다."
    async with c.uow() as uow:
        history = await uow.applications.history(application_id)
    if not history:
        return f"{application_id}: 기록이 없습니다."
    latest = history[-1]
    detail = f"{application_id}: {latest.state}"
    if latest.reason:
        detail += f" ({latest.reason})"
    if latest.scheduled_at:
        detail += f", 예약: {latest.scheduled_at.isoformat()}"
    if latest.submitted_at:
        detail += f", 제출: {latest.submitted_at.isoformat()}"
    return detail


async def _list_recipe_versions(args: dict[str, str], c: Container, _client: Client) -> str:
    platform = args.get("platform", "").strip()
    if not platform:
        return "platform이 필요합니다."
    versions = await c.recipes.versions(platform)
    if not versions:
        return f"{platform}: 등록된 recipe 버전이 없습니다."
    return "\n".join(f"v{r.version}: {r.status}" for r in versions)


async def _resend_pending_decision(args: dict[str, str], c: Container, client: Client) -> str:
    application_id = args.get("application_id", "").strip()
    if not application_id:
        return "application_id가 필요합니다."
    try:
        handle = client.get_workflow_handle(f"application-{application_id}")
        view = await handle.query(ApplicationWorkflow.pending_decision)
    except RPCError as e:
        return f"{application_id}: 워크플로우를 찾을 수 없습니다 ({e.message})"
    if not view.has_pending:
        return f"{application_id}: 대기 중인 승인이 없습니다."
    # 이 함수는 c.settings.notifier == "telegram" 일 때만 불린다(bridge.handle_message 가 먼저
    # 걸러준다) — bridge.py 의 `_telegram(c)` 와 같은 불변식.
    assert isinstance(c.notifier, _ResendableNotifier)
    await c.notifier.resend_decision(application_id, view.nonce)
    return f"{application_id}: 승인 버튼을 다시 보냈습니다."


async def _start_applications(args: dict[str, str], c: Container, client: Client) -> str:
    count = _parse_apply_count(args.get("count", ""))
    dry_run = _parse_bool(args.get("dry_run", ""))
    result = await start_actionable_applications(count, c, client, dry_run=dry_run)
    if result.candidates == 0:
        return "최근 24시간 내 수집된 지원 가능 공고가 없습니다 (공고 수집부터 필요합니다)."
    if not result.started and not result.skipped:
        return "지원 가능한 공고는 있지만 하나도 시작하지 못했습니다."
    if result.dry_run:
        verb = "선택됐을 겁니다 (dry run — 실제로 워크플로우를 시작하지 않았습니다)"
    else:
        verb = "지원 워크플로우를 시작했습니다 (제출 전 승인 요청이 옵니다)"
    lines = [f"{len(result.started)}건 {verb}:"]
    lines += [f"  - {label}" for label in result.started]
    if result.skipped:
        lines.append(
            f"이미 진행 중이거나 지원했던 {len(result.skipped)}건은 건너뜀: "
            + ", ".join(result.skipped)
        )
    return "\n".join(lines)


async def _apply_by_url(args: dict[str, str], c: Container, client: Client) -> str:
    url = args.get("url", "").strip()
    if not url:
        return "url이 필요합니다."
    result = await apply_by_url(url, c, client)
    match result.outcome:
        case "unsupported_platform" | "not_found":
            return result.detail or "처리할 수 없습니다."
        case "duplicate":
            return f"{result.label}: 이미 지원 이력이 있거나 진행 중입니다."
        case "started":
            return f"{result.label}: 지원 워크플로우를 시작했습니다 (제출 전 승인 요청이 옵니다)."
    raise AssertionError(f"unreachable outcome: {result.outcome}")


# name -> (description, arg 이름들, handler). 늘어날 걸 전제로 한 레지스트리 — 새 도구는
# 여기 항목 하나 추가로 끝난다.
TOOLS: dict[str, tuple[str, tuple[str, ...], ToolHandler]] = {
    "list_applications": (
        "최근 지원 건 목록(상태 포함)을 최신순으로 본다",
        ("limit",),
        _list_applications,
    ),
    "get_application": (
        "특정 지원 건 하나의 최신 상태를 조회한다",
        ("application_id",),
        _get_application,
    ),
    "list_recipe_versions": (
        "특정 플랫폼의 recipe 버전 목록과 각 상태(draft/candidate/active/deprecated)를 본다",
        ("platform",),
        _list_recipe_versions,
    ),
    "resend_pending_decision": (
        "승인 대기 중인 지원 건의 승인/거절/수정요청 버튼을 다시 보낸다"
        " (직접 승인/거절하지 않는다 — 사람이 버튼을 눌러야 한다)",
        ("application_id",),
        _resend_pending_decision,
    ),
    "start_applications": (
        "최근 24시간 내 수집된 지원 가능 공고 중 적합도 상위 N건에 대해 지원 워크플로우를 새로"
        " 시작한다(이미 시작했던 공고는 자동으로 건너뜀). '3건 지원해줘', '제출해줘 5개' 같은"
        " 요청에 쓴다. 실제 제출은 여전히 사람이 텔레그램 승인 버튼을 눌러야 일어난다 — 이"
        " 도구는 워크플로우 시작(=이력서 생성·승인 대기 진입)까지만 한다. dry_run을 true로"
        " 주면 실제로 아무것도 시작하지 않고 어떤 공고가 선택될지만 보여준다(테스트/확인용,"
        " '일단 뭐가 뽑히는지만 보여줘', '테스트로 해봐' 같은 요청에 쓴다)",
        ("count", "dry_run"),
        _start_applications,
    ),
    "apply_by_url": (
        "특정 원티드(wanted.co.kr) 공고 링크 하나에 대해 지원 워크플로우를 새로 시작한다"
        " (이미 지원했거나 진행 중이면 건너뛴다). '이 링크 지원해줘', '이거 지원해줘 <url>'"
        " 처럼 사용자가 공고를 직접 지정할 때 쓴다 — start_applications 와 달리 적합도로"
        " 자동 선정하지 않고 사용자가 준 URL 하나만 처리한다. 원티드 링크가 아니면 처리하지"
        " 않는다. 실제 제출은 여전히 사람이 텔레그램 승인 버튼을 눌러야 일어난다.",
        ("url",),
        _apply_by_url,
    ),
}


def catalog() -> list[ToolCatalogEntry]:
    return [ToolCatalogEntry(name, desc, args) for name, (desc, args, _) in TOOLS.items()]
