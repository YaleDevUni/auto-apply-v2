"""저장된 자격증명(.env)으로 자동 재로그인해 storage_state 를 갱신한다.

`scripts/save_auth_state.py`(사람이 직접 로그인)를 대체하는 게 아니라 보완한다 — 세션이
자주 만료되는 플랫폼(사람인, `saramin-recipe-progress` 메모리)에서 매번 사람을 부르지 않기
위한 자동 갱신 경로다. CLAUDE.md 정책 변경(2026-08-21, 사용자 확정): "비밀번호를 코드가
타이핑하지 않는다" 규칙을 "본인 계정 자격증명을 .env 에 두고 코드가 로그인 폼에 입력하는 것"
까지 완화했다 — 단, CAPTCHA/추가 인증(SMS 등)은 여전히 우회하지 않는다
(`domain/login_flow.detect_login_outcome`). 그 경우엔 이 스크립트가 실패로 끝나고, 사람이
`save_auth_state.py`로 직접 로그인해야 한다.

사용법:
    uv run python scripts/auto_login.py saramin
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TypedDict

from playwright.async_api import async_playwright

from auto_apply.config import load_settings
from auto_apply.domain.login_flow import LoginOutcome, detect_login_outcome


class LoginFlow(TypedDict):
    url: str
    id_selector: str
    password_selector: str
    keep_logged_in_selector: str
    submit_selector: str
    login_path_marker: str  # 로그인 실패 시 URL 에 남아있는 경로 조각


# 사람인은 headless 연결을 WAF 가 끊는다는 게 실측 확인됐다(saramin-recipe-progress 메모리) —
# 그래서 이 스크립트는 플랫폼과 무관하게 항상 headed 로 띄운다.
LOGIN_FLOWS: dict[str, LoginFlow] = {
    "saramin": {
        "url": "https://www.saramin.co.kr/zf_user/auth",
        "id_selector": "#id",
        "password_selector": "#password",
        "keep_logged_in_selector": "#autologin",  # "로그인 유지" — 세션을 최대한 오래 살린다
        "submit_selector": "button.btn_login",
        "login_path_marker": "/zf_user/auth",
    },
}


async def auto_login(platform: str, out_dir: Path) -> int:
    flow = LOGIN_FLOWS[platform]
    settings = load_settings()
    username = getattr(settings, f"{platform}_username", "")
    password = getattr(settings, f"{platform}_password", "")
    if not username or not password:
        env_prefix = platform.upper()
        print(
            f"[auto_login] {env_prefix}_USERNAME/{env_prefix}_PASSWORD 가 .env 에 없다",
            file=sys.stderr,
        )
        return 1

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(flow["url"])
        await page.fill(flow["id_selector"], username)
        await page.fill(flow["password_selector"], password)
        # 사람인은 체크박스를 시각적으로 커스텀 라벨로 덮어놔서 일반 클릭이 라벨에 가로채인다
        # (실측) — force=True 로 클릭 가능성 검사를 건너뛰고 상태만 맞춘다.
        await page.check(flow["keep_logged_in_selector"], force=True)
        await page.click(flow["submit_selector"])
        await page.wait_for_load_state("networkidle")

        html = await page.content()
        still_on_login_page = flow["login_path_marker"] in page.url
        outcome = detect_login_outcome(still_on_login_page=still_on_login_page, html=html)

        exit_code = 0
        if outcome is LoginOutcome.SUCCESS:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{platform}.json"
            await context.storage_state(path=str(out_path))
            print(f"[auto_login] 로그인 성공, 저장됨: {out_path}")
        elif outcome is LoginOutcome.CAPTCHA:
            print(
                "[auto_login] CAPTCHA(또는 추가 인증) 감지 — 우회하지 않는다. "
                "scripts/save_auth_state.py 로 사람이 직접 로그인해라.",
                file=sys.stderr,
            )
            exit_code = 2
        elif outcome is LoginOutcome.INVALID_CREDENTIALS:
            print("[auto_login] 아이디/비밀번호가 틀렸다 — .env 확인 필요.", file=sys.stderr)
            exit_code = 3
        else:
            print(
                f"[auto_login] 로그인 결과를 판별 못함(url={page.url}) — 수동 확인 필요.",
                file=sys.stderr,
            )
            exit_code = 4

        await browser.close()
        return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=list(LOGIN_FLOWS))
    parser.add_argument("--out-dir", type=Path, default=Path("./var/auth"))
    args = parser.parse_args()
    sys.exit(asyncio.run(auto_login(args.platform, args.out_dir)))
