from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Credential:
    username: str
    password: str


class CredentialSource(Protocol):
    """ATS/자체구축 로그인 계정 큐 (ARCHITECTURE.md §2.4b).

    같은 ATS 플랫폼이라도 회사마다 별도 계정을 발급하는 경우가 많아, 도메인 기준(브라우저
    자체 비밀번호 매니저 등)으로는 어느 계정을 써야 할지 특정할 수 없다 — 실측으로 확인:
    Aside 자체 매니저가 도메인만 보고 "이 계정을 저장할까?"를 묻는데, 회사 A/B가 같은 ATS
    도메인을 쓰면 계정이 섞인다. 그래서 이 포트는 회사명(정규화된 키)으로 조회한다.

    **레이어링 규칙**: 이 포트가 돌려주는 `Credential`은 Temporal 활동 경계를 절대 넘으면
    안 된다(= `contracts/`에 두지 않는다) — 활동 반환값은 Temporal event history에 영구
    기록되므로, 평문 비밀번호가 거기 남으면 안 된다. `CredentialSource`는 `WebAgentExecutor`
    구현체 생성자에 주입되어 그 구현체 **내부에서만** 호출된다(`AttemptRepository`/`BlobStore`
    가 activity 구현에 주입되는 것과 같은 자리). 어떤 workflow/contracts DTO도 `Credential`을
    필드로 갖지 않는다 — Recipe의 `value_ref`(LLM 프롬프트에 값 대신 참조만 흘리는 것)와
    같은 철학을 활동 경계에도 그대로 적용한 것.
    """

    async def get(self, key: str) -> Credential | None: ...
