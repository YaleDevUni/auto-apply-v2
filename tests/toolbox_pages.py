"""BrowserToolbox·PageDriver 테스트 페이지 (tests/fixtures/toolbox/) 와 그 메모리 대역.

실제 드라이버는 짐 서버(`GymServer(root=TOOLBOX_DIR)`)가 서빙하는 HTML 을 열고, 대역은 같은 페이지를
`FakeDocument` 로 흉내 낸다 — contract test 가 둘에 같은 기대를 건다. HTML 을 바꾸면 여기도 바꾼다.
"""

from pathlib import Path

from auto_apply.adapters.browser.fake_effects import ChooseFile, Dialog, Send, SubmitForm
from auto_apply.adapters.browser.fake_pages import FakeDocument, FakeElement

TOOLBOX_DIR = Path(__file__).resolve().parent / "fixtures" / "toolbox"
FAKE_BASE = "http://fake.test"

FORM = "/sites/form.html"
NEXT = "/sites/next.html"
FRAME = "/sites/frame.html"
INNER = "/sites/inner.html"
HANDOFF = "/sites/handoff.html"
# 가드 이름을 먼저 차지하는 페이지 — 실제 드라이버 전용(대역은 스크립트 층이 없다)
PREEMPT = "/sites/preempt.html"

PASSWORD_VALUE = "prefilled-secret-pw"
OTP_VALUE = "otp-778899"


def _form(base: str) -> FakeDocument:
    return FakeDocument(
        title="도구 테스트 지원서",
        elements=[
            FakeElement("heading", "지원서", tag="h1", level=1),
            FakeElement("text", "모든 항목을 입력하세요.", tag="p"),
            FakeElement("textbox", "이름"),
            FakeElement("textbox", "이메일", type="email"),
            FakeElement("textbox", "자기소개", tag="textarea"),
            FakeElement(
                "combobox",
                "경력",
                tag="select",
                options=("선택", "1년 미만", "1~3년"),
                value="선택",
            ),
            FakeElement("checkbox", "개인정보 수집 동의", type="checkbox"),
            FakeElement("text", "근무 형태", tag="legend"),
            FakeElement("radio", "정규직", type="radio", group="kind"),
            FakeElement("radio", "계약직", type="radio", group="kind"),
            FakeElement("textbox", "비밀번호", type="password", value=PASSWORD_VALUE),
            FakeElement("textbox", "인증번호", autocomplete="one-time-code", value=OTP_VALUE),
            FakeElement("file", "이력서", type="file"),
            FakeElement("file", "포트폴리오", type="file", visible=False),
            FakeElement(
                "button",
                "지원하기",
                tag="button",
                type="submit",
                in_form=True,
                default_button=True,
                on_click=(SubmitForm("POST", base + "/api/toolbox/submit"),),
            ),
            FakeElement(
                "link",
                "다음 페이지",
                tag="a",
                href="next.html",
                on_click=(Send("GET", base + NEXT, navigation=True),),
            ),
        ],
    )


def _handoff(base: str) -> FakeDocument:
    return FakeDocument(
        title="핸드오프 테스트",
        elements=[
            FakeElement("heading", "사람 핸드오프", tag="h1", level=1),
            FakeElement(
                "button",
                "확인 창",
                tag="button",
                type="button",
                on_click=(
                    Dialog(
                        "confirm",
                        "계속할까요?",
                        then=(Send("POST", base + "/api/toolbox/confirmed"),),
                    ),
                ),
            ),
            FakeElement("file", "", type="file", visible=False),
            FakeElement(
                "button", "파일 선택", tag="button", type="button", on_click=(ChooseFile(),)
            ),
            FakeElement("text", "선택 없음", tag="p"),
            FakeElement("textbox", "이름", in_form=True),
            FakeElement(
                "button",
                "지원하기",
                tag="button",
                type="submit",
                in_form=True,
                default_button=True,
                on_click=(SubmitForm("POST", base + "/api/toolbox/submit"),),
            ),
        ],
    )


def fake_sites(base: str = FAKE_BASE) -> dict[str, FakeDocument]:
    return {
        base + FORM: _form(base),
        base + HANDOFF: _handoff(base),
        base + NEXT: FakeDocument(
            title="두 번째 페이지",
            elements=[
                FakeElement("heading", "추가 정보", tag="h1", level=1),
                FakeElement("textbox", "희망 연봉"),
                FakeElement("text", "불러오기 완료", tag="p"),
            ],
        ),
        base + FRAME: FakeDocument(
            title="임베드 지원서",
            elements=[
                FakeElement("heading", "채용 공고", tag="h1", level=1),
                FakeElement("textbox", "포트폴리오 URL", type="url", frame=1),
            ],
            frames=(base + INNER,),
        ),
    }
