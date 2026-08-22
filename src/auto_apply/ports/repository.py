from types import TracebackType
from typing import Protocol, Self

from auto_apply.contracts.dto import (
    ApplicationAttempt,
    ApplicationSummary,
    PersistState,
    ScheduleConfig,
)
from auto_apply.contracts.job import JobRecord
from auto_apply.domain.enums import ApplicationState


class ApplicationRepository(Protocol):
    async def upsert_state(self, state: PersistState) -> None:
        """(application_id, workflow_run_id, state) 기준 멱등 upsert (§4.1).

        activity 는 최소 1회 실행이므로 같은 값으로 두 번 불려도 결과가 같아야 한다.
        """
        ...

    async def history(self, application_id: str) -> list[PersistState]:
        """상태 전이 이력. 감사 로그 겸 테스트 검증용."""
        ...

    async def list_recent(self, limit: int = 10) -> list[ApplicationSummary]:
        """최근 갱신된 지원 건 상위 `limit`개, 각 건의 최신 상태만 (텔레그램 채팅 에이전트의

        `list_applications` 도구가 쓴다 — telegram/agent.py). "최근"의 기준은 구현마다 다르다
        (postgres 는 history row 의 자동증가 id, file 은 파일 mtime) — 정확한 정렬 보장이
        필요한 용도가 아니라 강한 계약을 두지 않는다.
        """
        ...

    async def latest_states(self, application_ids: list[str]) -> dict[str, ApplicationState]:
        """주어진 id들 중 이력이 있는 것만, 최신 상태로. `apply_intake.py`가 "이미 지원

        시작했거나 진행 중인 공고는 후보에서 미리 제외"할 때 쓴다 — id 하나씩
        `history()`를 부르면 후보 수만큼 N+1 쿼리가 되므로 배치로 받는다.
        """
        ...


class JobRepository(Protocol):
    """`JobCollectionWorkflow`가 쓰는 유일한 통로 (§4.1과 같은 원칙 — 진행 상태는

    activity 밖에서 직접 쓰지 않는다). `(platform, platform_job_id)`가 유일키다.
    """

    async def upsert(self, record: JobRecord) -> None:
        """멱등. 같은 공고를 다시 수집해도 행이 늘지 않고 최신 판정으로 갱신된다."""
        ...

    async def get(self, platform: str, platform_job_id: str) -> JobRecord | None: ...

    async def actionable(self) -> list[JobRecord]:
        """`applicability.actionable`이 True인 것만. 다음 단계(승인 대기열)가 읽는다."""
        ...


class AttemptRepository(Protocol):
    """`application_attempts` 감사 로그 (§4, §5). 실행 1회 = 1행."""

    async def record(self, attempt: ApplicationAttempt) -> None:
        """(application_id, attempt) 기준 멱등 upsert.

        같은 시도 번호로 여러 번 불려도(진행 중 → 최종 상태) 행이 늘지 않고 최신 값으로
        덮어써야 한다 — activity 재시도와, submit 전/후 두 번 기록하는 패턴 둘 다 이걸 요구한다.
        """
        ...

    async def history(self, application_id: str) -> list[ApplicationAttempt]: ...


class ScheduleConfigRepository(Protocol):
    """공고 수집/자동 지원 Schedule 설정 — target(`"collection"`/`"apply"`) 당 최신값 1건뿐이라

    이력이 없다(§ apply-schedule). 채팅(`telegram/_agent_tools_schedule.py`)이 시각/건수를
    바꾸면 여기 쓰고, `schedule_config.py`가 그 값을 Temporal Schedule에 그대로 밀어넣는다.
    """

    async def get(self, target: str) -> ScheduleConfig | None: ...

    async def set(self, config: ScheduleConfig) -> None:
        """target 기준 upsert — 멱등."""
        ...


class UnitOfWork(Protocol):
    # @property 로 선언한다. Protocol 의 일반 속성은 invariant 로 취급되어
    # 구현체가 더 구체적인 타입을 노출하면 타입 체크에 실패한다.
    @property
    def applications(self) -> ApplicationRepository: ...

    @property
    def jobs(self) -> JobRepository: ...

    @property
    def attempts(self) -> AttemptRepository: ...

    @property
    def schedule_config(self) -> ScheduleConfigRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
