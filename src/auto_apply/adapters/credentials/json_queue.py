"""회사명으로 키집한 로그인 계정 큐 — JSON 파일 (ARCHITECTURE.md §2.4b).

`FactSource`/`GuideSource`와 같은 패턴: 캐시 없이 매번 새로 읽는다. 이 파일(`config/
credentials.json`)은 `config/facts.yaml`/`profile.yaml`과 같은 이유로 gitignore 대상이다 —
실제 계정 정보라 커밋하지 않는다.
"""

import json
from pathlib import Path

import structlog

from auto_apply.domain.job_applicability import KNOWN_ATS
from auto_apply.ports.credentials import Credential

logger = structlog.get_logger()

# 이 키들과 (대소문자 무시) 일치하는 회사명은 거부한다 — "회사명이 플랫폼명이 되면 안 된다"는
# 안전장치. 실수로 job.company 대신 job.platform 을 키로 넘긴 버그를 조기에 잡는다.
_KNOWN_PLATFORMS = frozenset({"wanted", "saramin", "jasoseol"})


def _normalize(key: str) -> str:
    return key.strip().casefold()


def _looks_like_platform_name(key: str) -> bool:
    normalized = _normalize(key)
    if normalized in _KNOWN_PLATFORMS:
        return True
    return any(normalized in (host, host.split(".")[0]) for host in KNOWN_ATS)


class JsonQueueCredentialSource:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def get(self, key: str) -> Credential | None:
        entries = self._load()
        return entries.get(_normalize(key))

    def _load(self) -> dict[str, Credential]:
        if not self._path.is_file():
            return {}
        raw = json.loads(self._path.read_text())
        entries: dict[str, Credential] = {}
        for company, value in raw.items():
            if _looks_like_platform_name(company):
                logger.warning(
                    "credential_key_looks_like_platform",
                    key=company,
                    path=str(self._path),
                )
                continue
            entries[_normalize(company)] = Credential(
                username=value["username"], password=value["password"]
            )
        return entries
