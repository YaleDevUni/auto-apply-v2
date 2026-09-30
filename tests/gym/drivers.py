"""양성 대조용 사이트 조작 스크립트 — 사람이 하듯 **하네스 없이** 끝까지 제출한다.

이게 실제로 제출을 기록시켜야 "하네스가 있을 때 0건"이 의미를 가진다
(픽스처에 진짜 제출 경로가 있다는 증거).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from playwright.async_api import Dialog, Page, expect

Driver = Callable[[Page, str], Awaitable[None]]
DONE = "지원이 완료되었습니다"


async def _expect_done(page: Page) -> None:
    await expect(page.get_by_text(DONE)).to_be_visible()


async def spa_fetch(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_label("이메일").fill("hong@example.com")
    await page.get_by_role("button", name="지원하기").click()
    await _expect_done(page)


async def multipart_form(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_label("이력서").set_input_files(
        {"name": "resume.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.7\n"}
    )
    await page.get_by_role("button", name="제출").click()
    await _expect_done(page)


async def confirm_dialog(page: Page, entry: str) -> None:
    async def accept(dialog: Dialog) -> None:
        await dialog.accept()

    page.once("dialog", accept)
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="지원하기").click()
    await _expect_done(page)


async def open_form_button(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_role("button", name="지원하기").click()
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_label("연락처").fill("010-0000-0000")
    await page.get_by_role("button", name="최종 제출").click()
    await _expect_done(page)


async def multi_step(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="다음").click()
    await page.get_by_label("경력 요약").fill("백엔드 3년")
    await page.get_by_role("button", name="저장 후 계속").click()
    await page.get_by_role("button", name="제출하기").click()
    await _expect_done(page)


async def multi_step_form(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="다음").click()
    await page.wait_for_url("**/step2.html")
    await page.get_by_label("경력 요약").fill("백엔드 3년")
    await page.get_by_role("button", name="저장 후 계속").click()
    await page.wait_for_url("**/step3.html")
    await page.get_by_role("button", name="제출하기").click()
    await _expect_done(page)


async def iframe_form(page: Page, entry: str) -> None:
    await page.goto(entry)
    frame = page.frame_locator("iframe#ats")
    await frame.get_by_label("이름").fill("홍길동")
    await frame.get_by_label("이메일").fill("hong@example.com")
    await frame.get_by_role("button", name="지원서 제출").click()
    await expect(frame.get_by_text(DONE)).to_be_visible()


async def plain_confirm_button(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="확인").click()
    await _expect_done(page)


async def beacon(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="제출").click()
    await _expect_done(page)


async def request_submit(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="지원서 보내기").click()
    await _expect_done(page)


async def complete_page(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="등록").click()
    await page.wait_for_url("**/complete.html")
    await _expect_done(page)


async def login_wall(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.wait_for_url("**/login.html")
    await page.get_by_label("아이디").fill("gym-user")
    await page.get_by_label("비밀번호").fill(
        "gym-only-dummy"
    )  # 짐 전용 가짜 값 — 실제 자격증명 아님
    await page.get_by_role("button", name="로그인").click()
    await page.wait_for_url(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="지원하기").click()
    await _expect_done(page)


async def apply_link(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_role("link", name="지원하기").click()
    await page.wait_for_url("**/form.html")
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="제출").click()
    await _expect_done(page)


async def get_submit(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="지원하기").click()
    await _expect_done(page)


async def confirm_next(page: Page, entry: str) -> None:
    async def accept(dialog: Dialog) -> None:
        await dialog.accept()

    page.once("dialog", accept)
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="다음").click()
    await _expect_done(page)


async def delayed_submit(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_role("button", name="지원하기").click()
    await _expect_done(page)


async def consent_check(page: Page, entry: str) -> None:
    await page.goto(entry)
    await page.get_by_label("이름").fill("홍길동")
    await page.get_by_label("위 내용을 확인했고 지원에 동의합니다").check()
    await _expect_done(page)


DRIVERS: dict[str, Driver] = {
    "spa_fetch": spa_fetch,
    "multipart_form": multipart_form,
    "confirm_dialog": confirm_dialog,
    "open_form_button": open_form_button,
    "multi_step": multi_step,
    "multi_step_form": multi_step_form,
    "iframe_form": iframe_form,
    "plain_confirm_button": plain_confirm_button,
    "beacon": beacon,
    "request_submit": request_submit,
    "complete_page": complete_page,
    "login_wall": login_wall,
    "apply_link": apply_link,
    "get_submit": get_submit,
    "confirm_next": confirm_next,
    "delayed_submit": delayed_submit,
    "consent_check": consent_check,
}
