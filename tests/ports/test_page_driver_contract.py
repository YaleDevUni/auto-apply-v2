"""PageDriver contract test (§A5, §A2) — 대역과 실제(Playwright, native)에 같은 기대를 건다."""

import pytest

from auto_apply.contracts.page import PageSnapshot, SnapshotNode, UploadFile
from auto_apply.domain.errors import PageActionFailed, PageFailure
from tests.toolbox_kit import DriverKit, _shared_real_kit, driver_kit
from tests.toolbox_pages import FORM, FRAME, INNER, NEXT, OTP_VALUE, PASSWORD_VALUE

__all__ = ["_shared_real_kit", "driver_kit"]
# native 는 모듈에서 Chrome 하나를 나눠 쓴다 — 픽스처와 테스트가 같은 이벤트 루프여야 한다.
pytestmark = pytest.mark.asyncio(loop_scope="module")

PDF = UploadFile(name="resume.pdf", content_type="application/pdf", data=b"%PDF-1.7\n")


def _by_name(snap: PageSnapshot, name: str, role: str | None = None) -> SnapshotNode:
    found = [n for n in snap.nodes if n.name == name and (role is None or n.role == role)]
    assert len(found) == 1, (name, [(n.role, n.name) for n in snap.nodes])
    return found[0]


def _ref(snap: PageSnapshot, name: str, role: str | None = None) -> str:
    ref = _by_name(snap, name, role).ref
    assert ref is not None
    return ref


async def _open(kit: DriverKit, path: str = FORM):
    page = await kit.host.page()
    await kit.driver.navigate(page, kit.url(path))
    return page, await kit.driver.snapshot(page)


async def _refused(reason: PageFailure, action) -> None:
    with pytest.raises(PageActionFailed) as e:
        await action
    assert e.value.reason is reason


async def test_snapshot_lists_fields_with_refs(driver_kit):
    _, snap = await _open(driver_kit)
    assert snap.url == driver_kit.url(FORM)
    assert snap.title == "도구 테스트 지원서"
    assert _by_name(snap, "지원서").role == "heading"
    assert _by_name(snap, "지원서").ref is None  # 본문·제목은 맥락일 뿐 조작 대상이 아니다
    assert _by_name(snap, "모든 항목을 입력하세요.").ref is None
    for name, role in [
        ("이름", "textbox"),
        ("이메일", "textbox"),
        ("자기소개", "textbox"),
        ("경력", "combobox"),
        ("개인정보 수집 동의", "checkbox"),
        ("정규직", "radio"),
        ("이력서", "file"),
        ("지원하기", "button"),
        ("다음 페이지", "link"),
    ]:
        assert _by_name(snap, name, role).ref is not None
    assert _by_name(snap, "경력").options == ("선택", "1년 미만", "1~3년")
    assert _by_name(snap, "포트폴리오").hidden is True  # 숨긴 파일 입력도 업로드할 수 있어야 한다
    refs = [n.ref for n in snap.nodes if n.ref]
    assert len(refs) == len(set(refs))


async def test_secret_field_values_never_appear(driver_kit):
    _, snap = await _open(driver_kit)
    for name in ("비밀번호", "인증번호"):
        node = _by_name(snap, name)
        assert node.secret is True
        assert node.value is None
    dumped = snap.model_dump_json()
    assert PASSWORD_VALUE not in dumped
    assert OTP_VALUE not in dumped


async def test_fill_then_snapshot_shows_value_under_new_refs(driver_kit):
    page, snap = await _open(driver_kit)
    await driver_kit.driver.fill(page, _ref(snap, "이름"), "홍길동")
    await driver_kit.driver.fill(page, _ref(snap, "자기소개"), "첫 줄\n둘째 줄")
    again = await driver_kit.driver.snapshot(page)
    assert _by_name(again, "이름").value == "홍길동"
    assert _by_name(again, "자기소개").value == "첫 줄\n둘째 줄"
    assert {n.ref for n in again.nodes if n.ref}.isdisjoint({n.ref for n in snap.nodes if n.ref})
    await _refused(
        PageFailure.STALE_REF, driver_kit.driver.fill(page, _ref(snap, "이름"), "옛 ref")
    )


async def test_select_by_label(driver_kit):
    page, snap = await _open(driver_kit)
    assert await driver_kit.driver.select(page, _ref(snap, "경력"), "1~3년") == "1~3년"
    assert _by_name(await driver_kit.driver.snapshot(page), "경력").value == "1~3년"


async def test_select_refusals(driver_kit):
    page, snap = await _open(driver_kit)
    d = driver_kit.driver
    await _refused(PageFailure.OPTION_NOT_FOUND, d.select(page, _ref(snap, "경력"), "10년"))
    await _refused(PageFailure.UNSUPPORTED_ELEMENT, d.select(page, _ref(snap, "이름"), "선택"))


async def test_check_and_radio(driver_kit):
    page, snap = await _open(driver_kit)
    d = driver_kit.driver
    await d.set_checked(page, _ref(snap, "개인정보 수집 동의"), True)
    await d.set_checked(page, _ref(snap, "정규직"), True)
    await d.set_checked(page, _ref(snap, "계약직"), True)
    again = await d.snapshot(page)
    assert _by_name(again, "개인정보 수집 동의").checked is True
    assert _by_name(again, "정규직").checked is False
    assert _by_name(again, "계약직").checked is True
    await d.set_checked(page, _ref(again, "개인정보 수집 동의"), False)
    assert _by_name(await d.snapshot(page), "개인정보 수집 동의").checked is False


async def test_check_refusals(driver_kit):
    page, snap = await _open(driver_kit)
    d = driver_kit.driver
    # radio 는 끌 수 없고, 버튼·링크는 check 로 누를 수 없다 (클릭은 하네스를 거친다, T2.5)
    await _refused(
        PageFailure.UNSUPPORTED_ELEMENT, d.set_checked(page, _ref(snap, "정규직"), False)
    )
    for name in ("지원하기", "다음 페이지", "이름"):
        await _refused(PageFailure.UNSUPPORTED_ELEMENT, d.set_checked(page, _ref(snap, name), True))


async def test_fill_refusals(driver_kit):
    page, snap = await _open(driver_kit)
    d = driver_kit.driver
    for name in ("비밀번호", "인증번호"):
        await _refused(PageFailure.SECRET_FIELD, d.fill(page, _ref(snap, name), "x"))
    for name in ("지원하기", "개인정보 수집 동의", "이력서", "경력"):
        await _refused(PageFailure.UNSUPPORTED_ELEMENT, d.fill(page, _ref(snap, name), "x"))


async def test_field_turned_secret_after_snapshot_is_refused(driver_kit):
    page, snap = await _open(driver_kit)
    await driver_kit.make_secret(page, "이름")
    await _refused(PageFailure.SECRET_FIELD, driver_kit.driver.fill(page, _ref(snap, "이름"), "x"))


async def test_upload_to_file_inputs_only(driver_kit):
    page, snap = await _open(driver_kit)
    d = driver_kit.driver
    await d.upload(page, _ref(snap, "이력서"), PDF)
    await d.upload(page, _ref(snap, "포트폴리오"), PDF)
    await _refused(PageFailure.UNSUPPORTED_ELEMENT, d.upload(page, _ref(snap, "이름"), PDF))


async def test_navigate_back_and_invalidated_refs(driver_kit):
    page, snap = await _open(driver_kit)
    d = driver_kit.driver
    await d.navigate(page, driver_kit.url(NEXT))
    await _refused(PageFailure.STALE_REF, d.fill(page, _ref(snap, "이름"), "x"))
    assert _by_name(await d.snapshot(page), "희망 연봉").ref is not None
    await d.back(page)
    assert (await d.snapshot(page)).url == driver_kit.url(FORM)


async def test_navigation_failures(driver_kit):
    page = await driver_kit.host.page()
    d = driver_kit.driver
    await _refused(PageFailure.NAVIGATION_FAILED, d.back(page))  # 새 탭엔 뒤가 없다
    await _refused(PageFailure.NAVIGATION_FAILED, d.navigate(page, "http://127.0.0.1:1/"))


async def test_iframe_fields_are_reachable(driver_kit):
    page, snap = await _open(driver_kit, FRAME)
    node = _by_name(snap, "포트폴리오 URL")
    assert node.frame == 1
    assert snap.frames[1] == driver_kit.url(INNER)
    assert node.ref is not None
    await driver_kit.driver.fill(page, node.ref, "https://example.com/me")
    assert _by_name(await driver_kit.driver.snapshot(page), "포트폴리오 URL").value == (
        "https://example.com/me"
    )


async def test_wait_for_text(driver_kit):
    page, _ = await _open(driver_kit, NEXT)
    assert await driver_kit.driver.wait_for_text(page, "불러오기 완료", timeout_ms=5_000)
    assert not await driver_kit.driver.wait_for_text(page, "없는 문구", timeout_ms=300)


async def test_scroll(driver_kit):
    page, snap = await _open(driver_kit)
    await driver_kit.driver.scroll(page, None, down=True)
    await driver_kit.driver.scroll(page, _ref(snap, "지원하기"), down=True)
    await _refused(PageFailure.STALE_REF, driver_kit.driver.scroll(page, "e999999", down=True))


async def test_closed_tab_is_stale(driver_kit):
    page, snap = await _open(driver_kit)
    await driver_kit.close_tabs(page)
    await _refused(PageFailure.STALE_REF, driver_kit.driver.fill(page, _ref(snap, "이름"), "x"))
