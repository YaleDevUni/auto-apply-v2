"""제출 클릭 분류기 픽스처 표 (§A4 L2).

표의 한 줄 = (기술자, 기대 판정). Risky 는 이유까지, Safe 는 근거까지 맞아야 한다 — 우연히 다른
규칙으로 맞는 것도 회귀로 본다.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from auto_apply.contracts.click import (
    ClickRisk,
    ClickVerdict,
    ElementDescriptor,
    RiskyClick,
    SafeBasis,
    SafeClick,
)
from auto_apply.domain.submit_classifier import classify_click

SUBMIT = ClickRisk.SUBMIT_TYPE
DEFAULT = ClickRisk.FORM_DEFAULT_BUTTON
DIALOG = ClickRisk.DIALOG_CONFIRM
WORD = ClickRisk.SUBMIT_WORD
UNKNOWN = ClickRisk.UNRECOGNIZED
INERT = SafeBasis.INERT_CONTROL
LINK = SafeBasis.NAVIGATION_LINK
SAFE = SafeBasis.SAFE_WORD


def btn(text: str = "", **kw: object) -> dict[str, object]:
    return {"tag": "button", "type": "button", "text": text, **kw}


def case(expected: ClickRisk | SafeBasis, id: str, **kw: object) -> object:
    return pytest.param(kw, expected, id=id)


RISKY_CASES = [
    # --- 제출 컨트롤: 라벨이 안전 어휘여도 type 이 이긴다
    case(SUBMIT, "submit-type-다음", tag="button", type="submit", text="다음", in_form=True),
    case(SUBMIT, "button-no-type-in-form", tag="button", text="저장", in_form=True),
    case(SUBMIT, "input-submit", tag="input", type="submit", name="Submit"),
    case(SUBMIT, "input-image", tag="input", type="image"),
    case(SUBMIT, "type-uppercase", tag="button", type="SUBMIT", text="Next"),
    case(SUBMIT, "unknown-button-type", tag="button", type="bogus", text="다음", in_form=True),
    case(
        SUBMIT,
        "fullwidth-button-type",
        tag="button",
        type="\uff42\uff55\uff54\uff54\uff4f\uff4e",
        text="다음",
    ),
    case(SUBMIT, "padded-button-type", tag="button", type=" button", text="다음"),
    case(SUBMIT, "submit-outside-form", tag="button", type="submit", text="파일 선택"),
    case(SUBMIT, "tag-uppercase", tag="INPUT", type="submit"),
    # --- 폼 기본 버튼
    case(
        DEFAULT,
        "default-button",
        tag="div",
        role="button",
        text="다음",
        is_form_default_button=True,
    ),
    # --- dialog 안의 긍정 응답
    case(DIALOG, "dialog-확인", **btn("확인", in_dialog=True)),
    case(DIALOG, "dialog-OK", **btn("OK", in_dialog=True)),
    case(DIALOG, "dialog-예", **btn(" 예 ", in_dialog=True)),
    case(DIALOG, "dialog-네", **btn("네", in_dialog=True)),
    case(DIALOG, "dialog-yes-name", **btn("Yes", name="Yes", in_dialog=True)),
    case(DIALOG, "dialog-계속", **btn("계속", in_dialog=True)),
    case(DIALOG, "dialog-continue", **btn("Continue", in_dialog=True)),
    # --- 제출 어휘 (한)
    case(WORD, "제출", **btn("제출")),
    case(WORD, "지원하기", **btn("지원하기")),
    case(WORD, "지원 완료", **btn("지원 완료")),
    case(WORD, "최종 제출", **btn("최종 제출")),
    case(WORD, "입사지원", **btn("입사지원")),
    case(WORD, "제 출-spaced", **btn("제 출")),
    case(WORD, "zero-width", **btn("지원​하기")),
    case(WORD, "fullwidth-space", **btn("지원\u3000하기")),
    case(WORD, "확인-outside-dialog", **btn("확인")),
    case(WORD, "등록하기", **btn("등록하기")),
    case(WORD, "저장 후 제출", **btn("저장 후 제출")),
    case(WORD, "name-beats-text", tag="div", role="button", name="제출", text="Next"),
    case(WORD, "동의하고 지원", **btn("동의하고 지원")),
    # --- 제출 어휘 (영)
    case(WORD, "Submit", **btn("Submit")),
    case(WORD, "SUBMIT APPLICATION", **btn("SUBMIT APPLICATION")),
    case(WORD, "fullwidth-SUBMIT", **btn("\uff33\uff35\uff22\uff2d\uff29\uff34")),
    case(WORD, "Apply now", **btn("Apply now")),
    case(WORD, "Send", **btn("Send")),
    case(WORD, "Finish", **btn("Finish")),
    case(WORD, "Confirm", **btn("Confirm")),
    case(WORD, "Save and submit", **btn("Save and submit")),
    case(WORD, "S u b m i t", **btn("S u b m i t")),
    case(WORD, "OK-outside-dialog", **btn("OK")),
    case(WORD, "Done", **btn("Done")),
    case(WORD, "Complete application", **btn("Complete application")),
    case(WORD, "I agree", **btn("I agree")),
    case(WORD, "link-href-apply", tag="a", href="/jobs/1/apply", text="자세히"),
    case(WORD, "link-href-percent", tag="a", href="/%73ubmit", text="자세히"),
    case(WORD, "checkbox-confirm-label", tag="input", type="checkbox", name="I confirm the above"),
    # --- 안전 근거 없음 → 닫힌 쪽
    case(UNKNOWN, "icon-button", **btn("")),
    case(UNKNOWN, "clickable-card", tag="div", text="홍길동"),
    case(UNKNOWN, "link-hash", tag="a", href="#", text="자세히"),
    case(UNKNOWN, "link-javascript", tag="a", href="javascript:void(0)", text="보기"),
    case(UNKNOWN, "link-javascript-split", tag="a", href="JAVA\tSCRIPT:go()", text="보기"),
    case(UNKNOWN, "link-mailto", tag="a", href="mailto:hr@example.com", text="보기"),
    case(UNKNOWN, "link-role-button", tag="a", role="button", href="/x", text="보기"),
    case(UNKNOWN, "cyrillic-lookalike", **btn("\u0405ave")),  # 키릴 \u0405 + ave
    case(UNKNOWN, "lone-jamo", **btn("다음ㅈ")),
    case(UNKNOWN, "input-button-unknown-word", tag="input", type="button", name="Go"),
    case(UNKNOWN, "input-unknown-type", tag="input", type="\uff54\uff45\uff58\uff54"),
    case(UNKNOWN, "dialog-unknown", **btn("알겠어요", in_dialog=True)),
]

SAFE_CASES = [
    case(INERT, "input-text", tag="input", type="text", name="이름"),
    case(INERT, "input-no-type", tag="input", in_form=True),
    case(INERT, "checkbox", tag="input", type="checkbox", name="뉴스레터 수신"),
    case(INERT, "radio", tag="input", type="radio", name="남"),
    case(INERT, "file-input", tag="input", type="file", name="파일 선택"),
    case(INERT, "textarea", tag="textarea", name="자기소개", in_form=True),
    case(INERT, "select", tag="select", name="학력"),
    case(INERT, "role-tab", tag="div", role="tab", text="경력"),
    case(INERT, "role-list-first-wins", tag="div", role="checkbox button", text="약관"),
    case(LINK, "link-absolute", tag="a", href="https://example.com/jobs/1", text="공고 보기"),
    case(LINK, "link-relative", tag="a", href="/careers", text="Careers"),
    case(SAFE, "다음", **btn("다음")),
    case(SAFE, "저장 후 계속", **btn("저장 후 계속")),
    case(SAFE, "파일 선택", **btn("파일 선택")),
    case(SAFE, "임시저장", **btn("임시저장")),
    case(SAFE, "학력 추가", **btn("학력 추가")),
    case(SAFE, "이전", **btn("이전")),
    case(SAFE, "다 음-spaced", **btn(" 다 음 ")),
    case(SAFE, "Next", **btn("Next")),
    case(SAFE, "fullwidth-Next", **btn("\uff2e\uff45\uff58\uff54")),
    case(SAFE, "zero-width-Next", **btn("Ne\u200bxt")),
    case(SAFE, "Save and continue", **btn("Save and continue")),
    case(SAFE, "Choose file", **btn("Choose file")),
    case(SAFE, "Upload resume", **btn("Upload resume")),
    case(SAFE, "dialog-취소", **btn("취소", in_dialog=True)),
    case(SAFE, "dialog-Close", **btn("Close", in_dialog=True)),
    case(SAFE, "button-no-type-outside-form", tag="button", text="다음"),
    case(SAFE, "input-button-다음", tag="input", type="button", name="다음"),
    case(SAFE, "div-role-button-다음", tag="div", role="button", text="다음"),
]


@pytest.mark.parametrize(("element", "reason"), RISKY_CASES)
def test_risky(element, reason):
    verdict = classify_click(ElementDescriptor.model_validate(element))
    assert isinstance(verdict, RiskyClick), verdict
    assert verdict.reason is reason


@pytest.mark.parametrize(("element", "basis"), SAFE_CASES)
def test_safe(element, basis):
    verdict = classify_click(ElementDescriptor.model_validate(element))
    assert isinstance(verdict, SafeClick), verdict
    assert verdict.basis is basis


def test_fixture_table_is_large_enough():
    assert len(RISKY_CASES) + len(SAFE_CASES) >= 40


@pytest.mark.parametrize("risky_word", ["제출", "지원하기", "submit", "apply"])
@pytest.mark.parametrize(("element", "basis"), [c for c in SAFE_CASES if c.values[1] is SAFE])
def test_risky_word_overrides_every_safe_label(element, basis, risky_word):
    tainted = {**element, "text": f"{element.get('text', '')} {risky_word}"}
    assert isinstance(classify_click(ElementDescriptor.model_validate(tainted)), RiskyClick)


def test_verdict_round_trips_as_discriminated_union():
    adapter = TypeAdapter(ClickVerdict)
    for verdict in (
        RiskyClick(reason=ClickRisk.SUBMIT_WORD, detail="제출"),
        SafeClick(basis=SafeBasis.SAFE_WORD, detail="다음"),
    ):
        assert adapter.validate_json(adapter.dump_json(verdict)) == verdict


def test_descriptor_is_strict_and_frozen():
    with pytest.raises(ValidationError):
        ElementDescriptor.model_validate({"tag": "button", "onclick": "submit()"})
    element = ElementDescriptor(tag="button")
    with pytest.raises(ValidationError):
        element.in_form = True  # type: ignore[misc]
