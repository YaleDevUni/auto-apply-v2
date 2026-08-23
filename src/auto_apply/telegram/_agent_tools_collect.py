"""공고 수집 즉시 실행 도구 — `telegram/_agent_tools.py`가 `TOOLS`에 병합한다.

"공고 수집을 즉시 실행하는 도구는 제 권한에 없습니다"라고 채팅 에이전트가 실제로 답한 걸
계기로 신설(2026-08-23). `start_applications`가 이미 즉시 지원을 "시작"할 수 있지만, 그게
고르는 후보 자체는 `JobCollectionWorkflow`가 Schedule로 채워둔 캐시(§ apply_intake.py
`JOB_CACHE_TTL`)뿐이라 "지금 새로 수집"은 스케줄을 기다리거나 사람이 셸에서 `cli.py collect`를
직접 돌려야 했던 갭이었다. `_agent_tools.py`/`apply_intake.py` 둘 다 이미 200줄을 넘어서
(§ CLAUDE.md "한 파일 = 한 책임") `_agent_tools_schedule.py`와 같은 이유로 새 파일로 뺀다.

platforms 인자는 안 받는다 — LLM이 플랫폼명을 오타·오해석할 위험을 피하는 선택(Recipe/가이드
patch/schedule_cron과 같은 "AI는 생성만, 조합·해석은 코드" 철학의 연장)이고,
`ScheduleConfig("collection")`에 이미 있는 값(채팅으로도 못 바꾸는, 스케줄과 동일한 플랫폼
목록 — `_agent_tools_schedule.py` 참고)을 그대로 재사용하면 "지금 당겨서 한 번 더 돈다"는
의미가 스케줄이 도는 것과 정확히 일치한다.

`cli.py`의 `collect` 명령과 동일하게 워크플로우 완료까지 동기 대기한다 — 텔레그램 쪽엔 이
응답을 기다리는 하드 타임아웃이 없고(웹훅/리스너 둘 다 자체 HTTP 왕복이 아니라 별도로 봇
API를 호출해 답장한다), 사람이 "지금 수집해줘"라고 물었으면 결과(찾음/통과/지원가능 건수)를
바로 알고 싶어한다는 판단. 실제 스크래핑이라 플랫폼 수만큼 시간이 걸릴 수 있다는 걸 도구
설명에 명시해 사용자가 기다림을 예상하게 한다.

인자가 없어 (도구, 인자) 완전 일치로 막는 `telegram/agent.py`의 일반 중복 방지만으로 한
턴에 두 번 도는 걸 막기 충분하다 — `start_applications`처럼 인자가 매번 달라 별도
`SINGLE_SHOT_TOOLS` 등록이 필요했던 경우와 다르다.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from temporalio.client import Client

from auto_apply.bootstrap import Container
from auto_apply.contracts.job import CollectJobsInput
from auto_apply.schedule_config import load_or_seed
from auto_apply.temporal_config import QUEUE_DEFAULT
from auto_apply.workflows.job_collection import JobCollectionWorkflow

ToolHandler = Callable[[dict[str, str], Container, Client], Awaitable[str]]


async def _collect_now(_args: dict[str, str], c: Container, client: Client) -> str:
    config = await load_or_seed(c, "collection")
    platforms = config.platforms or []
    if not platforms:
        return "수집할 플랫폼이 설정돼 있지 않습니다."
    # cli.py `collect` 명령과 같은 workflow id 패턴("job-collection-{시각}") — 여기는 접두어로
    # 채팅발 수동 트리거임을 구분한다. Temporal workflow id 를 만들 뿐 workflow 코드가 아니라
    # datetime.now()를 써도 결정성 문제가 없다(§ CLAUDE.md, 이 파일은 운영 진입점).
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    handle = await client.start_workflow(
        JobCollectionWorkflow.run,
        CollectJobsInput(platforms=platforms),
        id=f"job-collection-chat-{run_id}",
        task_queue=QUEUE_DEFAULT,
    )
    result = await handle.result()
    lines = [
        f"{r.platform}: found={r.found} passed={r.passed} actionable={r.actionable}"
        + (f" error={r.error}" if r.error else "")
        for r in result.results
    ]
    return "공고 수집 완료\n" + "\n".join(lines)


COLLECT_TOOLS: dict[str, tuple[str, tuple[str, ...], ToolHandler]] = {
    "collect_now": (
        "공고 수집을 스케줄을 기다리지 않고 지금 바로 1회 실행한다(스케줄과 같은 플랫폼 목록"
        " 사용, 완료까지 몇 분 정도 걸릴 수 있다). '지금 공고 수집해줘', '새로 수집해줘' 같은"
        " 요청에 쓴다. 수집만 하고 지원 워크플로우는 시작하지 않는다 — 이어서 지원하려면"
        " start_applications 를 따로 불러야 한다.",
        (),
        _collect_now,
    ),
}
