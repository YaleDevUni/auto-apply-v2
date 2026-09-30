"""양성 대조 — D17 단계 이동 사이트(T2.7)를 사람처럼 **하네스 없이** 끝까지 제출한다.

`drivers.DRIVERS` 가 합친다. 마지막 버튼 라벨이 "다음"·"계속" 이어도 진짜 최종 제출 경로라는 증거.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from playwright.async_api import Page, expect

# drivers.DONE 과 같다 — drivers 가 이 모듈을 import 하므로 순환을 피해 따로 둔다
_DONE = "지원이 완료되었습니다"


async def next_final_signal(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="다음").click()
    await page.wait_for_url("**/step2.html")
    await page.get_by_label("자기소개").fill("백엔드 3년")
    await page.get_by_role("button", name="다음").click()
    await expect(page.get_by_text(_DONE)).to_be_visible()


async def next_final_nosignal(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="다음").click()
    await expect(page.get_by_text(_DONE)).to_be_visible()


async def review_page(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="다음").click()
    await page.wait_for_url("**/review.html")
    await page.get_by_role("button", name="계속").click()
    await expect(page.get_by_text(_DONE)).to_be_visible()


STEP_DRIVERS: dict[str, Callable[[Page, str], Awaitable[None]]] = {
    "next_final_signal": next_final_signal,
    "next_final_nosignal": next_final_nosignal,
    "review_page": review_page,
}
