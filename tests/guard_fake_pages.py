"""SubmitGuard 대역 테스트의 메모리 페이지 — 요소 핸들러를 fake_effects 로 적는다 (§A4)."""

from auto_apply.adapters.browser.fake_effects import Dialog, Later, Send, Show, SubmitForm
from auto_apply.adapters.browser.fake_pages import FakeDocument, FakeElement
from auto_apply.contracts.click import ElementDescriptor

B = "http://site.test"
FORM, NEXT, DONE = f"{B}/form", f"{B}/next", f"{B}/done"


def _form() -> FakeDocument:
    save = Send("POST", f"{B}/api/save")
    final = Send("POST", f"{B}/api/submit")
    return FakeDocument(
        title="지원서",
        elements=[
            FakeElement("heading", "지원서", tag="h1"),
            FakeElement("textbox", "이름"),
            FakeElement("button", "다음", tag="button", type="button", on_click=(save,)),
            FakeElement("button", "지원하기", tag="button", type="button", on_click=(final,)),
            FakeElement(
                "button", "제출", tag="button", type="submit", in_form=True, default_button=True,
                on_click=(SubmitForm("POST", f"{B}/api/submit"),),
            ),
            FakeElement(
                "button", "저장", tag="button", type="button",
                on_click=(Dialog("confirm", "정말 제출할까요? 900101-1234567", (final,)),),
            ),
            FakeElement("button", "늦게", tag="button", type="button",
                        on_click=(Later((final,)),)),  # 라벨이 안전 어휘가 아니다 → strict
            FakeElement("button", "보내기 예약", tag="button", type="button",
                        on_click=(Later((final,)),)),
            FakeElement("checkbox", "지원 내용에 동의", type="checkbox", on_change=(final,)),
            FakeElement("checkbox", "뉴스레터", type="checkbox", on_change=(save,)),
            FakeElement(
                "checkbox", "선택", tag="span",
                ancestors=(ElementDescriptor(tag="button", type="submit", in_form=True),),
                on_click=(final,),
            ),
            FakeElement("button", "쿼리 이동", tag="button", type="button",
                        on_click=(Send("GET", f"{NEXT}?name=x", navigation=True),)),
            FakeElement("button", "끝", tag="button", type="button",
                        on_click=(Show("지원해주셔서 감사합니다"),)),
            FakeElement("link", "다음 페이지", tag="a", href=NEXT,
                        on_click=(Send("GET", NEXT, navigation=True),)),
            FakeElement("button", "앱 호출", tag="button", type="button",
                        on_click=(Send("POST", "http://localhost:8000/api/approve"),)),
            FakeElement("button", "다시 그리기", tag="button", type="button",
                        on_click=(Show("지원서", role="heading"),)),
            # 안전 라벨이지만 입력값을 실어 문서를 연다(GET 폼 제출) / 요청만 보낸다(자동 완성)
            FakeElement("button", "검색", tag="button", type="button",
                        on_click=(Send("GET", f"{NEXT}?name=홍길동", navigation=True),)),
            FakeElement("button", "주소 검색", tag="button", type="button",
                        on_click=(Send("GET", f"{B}/api/address?q=홍길동"),)),
        ],
    )  # fmt: skip


def guard_sites() -> dict[str, FakeDocument]:
    return {
        FORM: _form(),
        NEXT: FakeDocument(title="2단계", elements=[FakeElement("textbox", "경력")]),
        DONE: FakeDocument(
            title="완료", elements=[FakeElement("heading", "지원이 완료되었습니다")]
        ),
    }
