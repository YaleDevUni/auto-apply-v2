"""wanted 쿠키 인증 어댑터가 공유하는 storage_state 로더.

`attachments/wanted.py`(첨부파일 정리)와 `platform/wanted.py`(제출확인, verify_submission)
둘 다 Recipe 실행(`PlaywrightExecutor`)과 같은 storage_state(`scripts/save_auth_state.py`로
사람이 만든 `var/auth/wanted.json`)를 쿠키로만 재사용한다 — 비밀번호를 코드가 타이핑하지
않는다(CLAUDE.md). 두 API 모두 쿠키 인증만으로 동작함을 실측 확인했다
(wanted-resume-list-cleanup-backlog, wanted-verify-submission-backlog).
"""

import json
from pathlib import Path

import httpx

from auto_apply.domain.errors import AuthRequired


def wanted_cookie_client(
    auth_dir: Path, *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """`var/auth/wanted.json`의 쿠키만 실어 인증된 httpx 클라이언트를 만든다.

    파일이 없으면 `AuthRequired` — 사람이 재로그인해야 풀린다(재시도로 안 풀리는 실패).
    """
    state_path = auth_dir / "wanted.json"
    if not state_path.is_file():
        raise AuthRequired(
            f"wanted 로그인 상태가 없다 — scripts/save_auth_state.py 로 먼저 로그인해라 "
            f"({state_path})"
        )
    state = json.loads(state_path.read_text())
    jar = httpx.Cookies()
    for c in state.get("cookies", []):
        jar.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
    return httpx.AsyncClient(cookies=jar, transport=transport, timeout=20.0)
