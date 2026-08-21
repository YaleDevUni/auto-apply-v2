"""사람인 쿠키 인증 어댑터가 공유하는 storage_state 로더 — `_wanted_auth.py`와 같은 패턴.

`platform/saramin.py`(verify_submission)가 Recipe 실행(`PlaywrightExecutor`)과 같은
storage_state(`var/auth/saramin.json`)를 쿠키로만 재사용한다. wanted 와 달리 UA/Referer 헤더
없인 `/zf_user/persons/apply-status-list` 가 로그인 페이지로 리다이렉트되는 걸 실측
확인했다(2026-08-21) — httpx 요청이라 브라우저 기본 헤더가 안 실리는 탓으로 추정.

세션이 만료돼 있으면(로그인 페이지로 리다이렉트) `scripts/auto_login.py saramin`으로 자동
재로그인해라 — CAPTCHA/추가 인증이 뜨면 그 스크립트가 실패하고, 그때는
`scripts/save_auth_state.py`로 사람이 직접 로그인해야 한다(saramin-recipe-progress 메모리,
CLAUDE.md 자동 로그인 정책).
"""

import json
from pathlib import Path

import httpx

from auto_apply.domain.errors import AuthRequired

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://www.saramin.co.kr/",
}


def saramin_cookie_client(
    auth_dir: Path, *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """`var/auth/saramin.json`의 쿠키만 실어 인증된 httpx 클라이언트를 만든다.

    파일이 없으면 `AuthRequired` — 사람이 재로그인해야 풀린다(재시도로 안 풀리는 실패).
    """
    state_path = auth_dir / "saramin.json"
    if not state_path.is_file():
        raise AuthRequired(
            f"사람인 로그인 상태가 없다 — scripts/auto_login.py saramin 로 먼저 로그인해라 "
            f"({state_path})"
        )
    state = json.loads(state_path.read_text())
    jar = httpx.Cookies()
    for c in state.get("cookies", []):
        jar.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
    return httpx.AsyncClient(
        cookies=jar, headers=_HEADERS, transport=transport, timeout=20.0, follow_redirects=True
    )
