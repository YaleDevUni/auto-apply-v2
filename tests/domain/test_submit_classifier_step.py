"""단계 이동 분류(D17) — STEP/Risky 경계와 마지막 단계 신호별 회귀 표.

STEP 은 "제출 컨트롤 + 라벨 전체가 단계 어휘 + 같은 페이지에 마지막 단계 신호 없음" 일 때만이다.
신호 하나하나가 STEP 을 Risky(LAST_STEP)로 되돌리는지, 애매한 라벨이 STEP 으로 새지 않는지 본다.
"""

import pytest

from auto_apply.contracts.click import (
    ClickRisk,
    ElementDescriptor,
    PageText,
    RiskyClick,
    SafeBasis,
    SafeClick,
    StepClick,
)
from auto_apply.domain.submit_classifier import classify_click, last_step_signal

# 입력칸이 있고 진행 표시·최종 문구가 없는 평범한 중간 단계 화면
MIDDLE = PageText(urls=("https://jobs.example.com/apply/1",), lines=("1단계 — 인적사항",), inputs=2)


def submit(text: str, **kw: object) -> ElementDescriptor:
    return ElementDescriptor.model_validate(
        {"tag": "button", "type": "submit", "text": text, "in_form": True, **kw}
    )


def page(*lines: str, inputs: int = 2, progress: tuple[tuple[int, int], ...] = ()) -> PageText:
    return PageText(lines=lines, inputs=inputs, progress=progress)


@pytest.mark.parametrize(
    "label",
    [
        "다음",
        "다음 단계",
        "다음 >",
        "저장 후 계속",
        "저장하고 다음",
        "계속하기",
        "Next",
        "NEXT →",
        "Continue",
        "Save & Continue",
        "Save and continue",
        "Next step",
        "\uff2e\uff25\uff38\uff34",
        "다 음",
    ],
)
def test_clear_step_label_on_a_middle_page_is_step(label):
    assert isinstance(classify_click(submit(label), MIDDLE), StepClick)


@pytest.mark.parametrize(
    "element",
    [
        pytest.param(
            ElementDescriptor(tag="input", type="submit", name="다음", in_form=True),
            id="input-submit",
        ),
        pytest.param(
            ElementDescriptor(tag="button", text="Next", in_form=True), id="button-without-type"
        ),
        pytest.param(
            ElementDescriptor(tag="div", text="다음", is_form_default_button=True),
            id="form-default-button",
        ),
    ],
)
def test_every_submit_control_kind_can_be_step(element):
    assert isinstance(classify_click(element, MIDDLE), StepClick)


@pytest.mark.parametrize(
    ("element", "reason"),
    [
        # 부분 일치는 STEP 이 아니다 — 라벨 전체가 단계 어휘여야 한다
        (submit("다음에 제출"), ClickRisk.SUBMIT_TYPE),
        (submit("Continue to review"), ClickRisk.SUBMIT_TYPE),
        (submit("Next: Submit"), ClickRisk.SUBMIT_TYPE),
        (submit("확인 후 다음"), ClickRisk.SUBMIT_TYPE),
        (submit("Proceed"), ClickRisk.SUBMIT_TYPE),
        # 단계를 넘기지 않는 저장·뒤로는 넣지 않았다
        (submit("저장"), ClickRisk.SUBMIT_TYPE),
        (submit("임시저장"), ClickRisk.SUBMIT_TYPE),
        (submit("이전"), ClickRisk.SUBMIT_TYPE),
        # 접근 이름과 보이는 글자가 다르면 둘 다 단계 어휘여야 한다
        (submit("다음", name="지원서 제출"), ClickRisk.SUBMIT_TYPE),
        (submit("Next", name="Submit application"), ClickRisk.SUBMIT_TYPE),
        # 글자 없는 버튼·이미지 버튼·dialog 안의 "계속" 은 STEP 이 아니다
        (submit(""), ClickRisk.SUBMIT_TYPE),
        (
            ElementDescriptor(tag="input", type="image", name="Next", in_form=True),
            ClickRisk.SUBMIT_TYPE,
        ),
        (submit("계속", in_dialog=True), ClickRisk.SUBMIT_TYPE),
        # 닮은꼴 글자(키릴 e — U+0435)는 어휘가 아니다
        (submit("N\u0435xt"), ClickRisk.SUBMIT_TYPE),
    ],
)
def test_ambiguous_submit_labels_stay_strict(element, reason):
    verdict = classify_click(element, MIDDLE)
    assert isinstance(verdict, RiskyClick) and verdict.reason is reason, verdict


def test_without_a_page_observation_there_is_no_step():
    # 신호가 없다고 볼 수 없으니 T2.2 판정 그대로 — 닫힌 쪽
    verdict = classify_click(submit("다음"))
    assert isinstance(verdict, RiskyClick) and verdict.reason is ClickRisk.SUBMIT_TYPE


LAST_STEP_PAGES = [
    # --- 진행 표시가 마지막 단계
    pytest.param(page(progress=((3, 3),)), "progress:dom", id="dom-aria-current-last"),
    pytest.param(page(progress=((1, 3), (4, 4))), "progress:dom", id="dom-any-marker-last"),
    pytest.param(page("단계 3/3"), "progress:단계 3/3", id="text-단계-3/3"),
    pytest.param(page("3 / 3 단계"), "progress:3 / 3 단계", id="text-3/3-단계"),
    pytest.param(page("3단계/3단계"), "progress:3단계/3단계", id="text-3단계/3단계"),
    pytest.param(page("총 3단계 중 3단계"), "progress:총 3단계 중 3단계", id="text-총-중"),
    pytest.param(page("Step 4 of 4"), "progress:step 4 of 4", id="text-step-of"),
    pytest.param(
        page("\uff33\uff34\uff25\uff30 \uff12\uff0f\uff12"),
        "progress:step 2/2",
        id="text-fullwidth",
    ),
    # --- 최종 동의·제출 전 확인 문구
    pytest.param(page("제출 전 입력 내용을 확인해 주세요"), "notice:제출전", id="ko-제출전"),
    pytest.param(page("최종 제출 전 확인"), "notice:제출전", id="ko-최종제출전"),
    pytest.param(page("제출 후에는 수정할 수 없습니다"), "notice:제출후에는", id="ko-제출후"),
    pytest.param(page("위 내용이 사실임을 확인합니다"), "notice:위내용이사실", id="ko-사실확인"),
    pytest.param(page("지원 내용 확인"), "notice:지원내용확인", id="ko-검토제목"),
    pytest.param(page("Please review your application"), None, id="en-review"),
    pytest.param(page("Once submitted, answers are final"), None, id="en-once"),
    pytest.param(page("I certify that the above is true"), None, id="en-certify"),
    pytest.param(page("By submitting you agree"), None, id="en-by-submitting"),
    # --- 편집 가능한 입력칸이 없는 화면(검토·요약)
    pytest.param(page("이름: 홍길동", "경력: 3년", inputs=0), "no_inputs", id="no-inputs"),
]


@pytest.mark.parametrize(("seen", "signal"), LAST_STEP_PAGES)
def test_each_last_step_signal_turns_step_into_risky(seen, signal):
    verdict = classify_click(submit("다음"), seen)
    assert isinstance(verdict, RiskyClick) and verdict.reason is ClickRisk.LAST_STEP
    if signal is not None:
        assert verdict.detail == signal
    else:
        assert verdict.detail.startswith("notice:")


@pytest.mark.parametrize(
    "seen",
    [
        pytest.param(page("1단계 — 인적사항"), id="step-heading"),
        pytest.param(page(progress=((2, 3),)), id="dom-middle"),
        pytest.param(page(progress=((1, 1),)), id="dom-single-item-list"),
        pytest.param(page("단계 2/3"), id="text-middle"),
        pytest.param(page("3/3"), id="bare-fraction-is-a-date"),
        pytest.param(page("최종학력"), id="최종학력-field"),
        pytest.param(page("지원이 완료되면 메일로 알려드립니다"), id="form-notice"),
        pytest.param(page("제출서류: 이력서"), id="제출서류"),
        pytest.param(page(inputs=1), id="one-input"),
    ],
)
def test_middle_pages_keep_step(seen):
    assert last_step_signal(seen) is None
    assert isinstance(classify_click(submit("다음"), seen), StepClick)


def test_safe_step_label_button_on_the_last_step_turns_risky():
    # type=button "다음" 이 fetch 로 최종 제출하는 마지막 단계 — relaxed 로 두지 않는다
    nxt = ElementDescriptor(tag="button", type="button", text="다음")
    assert classify_click(nxt, MIDDLE) == SafeClick(basis=SafeBasis.SAFE_WORD, detail="다음")
    last = classify_click(nxt, page("Step 2 of 2"))
    assert isinstance(last, RiskyClick) and last.reason is ClickRisk.LAST_STEP
    # 단계 어휘가 아닌 안전 버튼(파일 선택·닫기)은 마지막 단계에서도 그대로 Safe
    for label in ("파일 선택", "닫기"):
        other = ElementDescriptor(tag="button", type="button", text=label)
        assert isinstance(classify_click(other, page(inputs=0)), SafeClick)


def test_risky_words_still_win_on_any_page():
    for label in ("제출하기", "지원하기", "Submit"):
        verdict = classify_click(submit(label), MIDDLE)
        assert isinstance(verdict, RiskyClick) and verdict.reason is ClickRisk.SUBMIT_TYPE
