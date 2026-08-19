from typing import Protocol

from auto_apply.contracts.matching_config import MatchingConfig


class MatchingConfigSource(Protocol):
    """하드컷/트랙/스코어링 규칙을 읽어온다.

    domain/job_screening.py 와 domain/job_applicability.py 는 이 값을 어디서
    가져왔는지 모른다 — 파일이든, 나중에 DB든 이 port 뒤에서 바뀔 수 있다.
    """

    async def load(self) -> MatchingConfig: ...
