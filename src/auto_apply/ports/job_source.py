from collections.abc import AsyncIterator
from typing import Protocol

from auto_apply.contracts.job import JobPosting


class JobSource(Protocol):
    """플랫폼별 공고 대량 수집. `PlatformAdapter`(ports/platform.py)와는 다른 축이다.

    `PlatformAdapter`는 URL 하나가 이미 있을 때(사람이 링크를 넣었을 때) 쓰고,
    `JobSource`는 아직 어떤 공고가 있는지 모를 때 목록 전체를 훑는다.
    `JobCollectionWorkflow`(§2, 다음 단계)가 스케줄에 맞춰 이 port 를 돌린다.

    새 플랫폼을 추가하려면 이 Protocol을 구현하는 클래스를 하나 더 만들고
    bootstrap.py 의 `_build_job_sources`에 등록하면 된다. 나머지 파이프라인
    (도메인 매칭 이하)은 그대로 동작한다 — "한 코드에서 플랫폼별로 분기"하던
    구 프로젝트의 실수를 반복하지 않기 위한 경계선이다.
    """

    @property
    def platform(self) -> str: ...

    def list_jobs(self) -> AsyncIterator[JobPosting]:
        """목록 API/HTML만으로 얻을 수 있는 정보만 채워 내보낸다.

        본문(description) 전체를 다 채우지 못할 수 있다. 상세 조회는 비용이 커서
        `enrich()`로 분리한다. 호출부(activity)가 1차 스크리닝을 통과한 것만
        골라 `enrich`를 부르는 순서를 스스로 정한다.
        """
        ...

    async def enrich(self, job: JobPosting) -> JobPosting:
        """상세 조회로 본문을 채운 새 JobPosting을 돌려준다.

        상세가 필요 없는 플랫폼(목록 카드에 이미 필요한 정보가 다 있는 경우)은
        입력을 그대로 돌려줘도 계약을 지킨 것이다. no-op도 유효한 구현이다.
        """
        ...
