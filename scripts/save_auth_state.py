"""수동 로그인 → storage_state 저장.

PlaywrightExecutor 는 이 파일이 만든 JSON 을 읽어 이미 로그인된 컨텍스트로 시작한다.
비밀번호를 코드가 타이핑하지 않는다 (CLAUDE.md 규칙) — 사람이 뜬 브라우저에서 직접 로그인한다.

사용법:
    uv run python scripts/save_auth_state.py wanted --url https://www.wanted.co.kr/login

브라우저가 뜨면 평소처럼 로그인한다. 로그인이 끝나면 --signal-file 로 지정한 파일을 만들면
(예: `touch <signal-file>`) 그 시점의 쿠키/localStorage 를 var/auth/<platform>.json 에 저장하고
브라우저를 닫는다. signal 없이 --timeout 초가 지나면 그 시점 상태로 저장하고 종료한다.
"""

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

POLL_SECONDS = 2


async def wait_for_login(page: object, out_path: Path, signal_file: Path, timeout: float) -> None:
    signal_file.parent.mkdir(parents=True, exist_ok=True)
    signal_file.unlink(missing_ok=True)

    print(f"[save_auth_state] 브라우저가 열렸다. 로그인을 마친 뒤 아래 명령을 실행해라:")
    print(f"[save_auth_state]   touch {signal_file}")
    print(f"[save_auth_state] (또는 {int(timeout)}초 뒤 현재 상태로 자동 저장한다)")

    waited = 0.0
    while not signal_file.exists():
        await asyncio.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        if waited >= timeout:
            print(f"[save_auth_state] {int(timeout)}초 초과 — 현재 상태로 저장한다")
            break


async def main(platform: str, url: str, out_dir: Path, signal_file: Path, timeout: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{platform}.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)

        await wait_for_login(page, out_path, signal_file, timeout)

        await context.storage_state(path=str(out_path))
        await browser.close()
        print(f"[save_auth_state] 저장됨: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", help="예: wanted")
    parser.add_argument("--url", required=True, help="로그인 페이지 URL")
    parser.add_argument("--out-dir", default="./var/auth")
    parser.add_argument(
        "--signal-file",
        default="/private/tmp/claude-501/-Users-yeilpark-Documents-GitHub-auto-apply-v2/8da81afb-6a21-4e95-af90-6d8e1cf187c9/scratchpad/login_done",
    )
    parser.add_argument("--timeout", type=float, default=1800.0, help="초 (기본 30분)")
    args = parser.parse_args()
    asyncio.run(
        main(args.platform, args.url, Path(args.out_dir), Path(args.signal_file), args.timeout)
    )
