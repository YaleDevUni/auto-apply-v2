"""domain/chat_agent.py — 프롬프트 조립 순수 함수(포트 무의존)."""

from auto_apply.ai.schemas import AgentStep
from auto_apply.domain.chat_agent import (
    ToolCatalogEntry,
    build_fallback_message,
    build_prompt,
    catalog_prefix,
)


def test_catalog_prefix_lists_tool_name_args_and_description():
    catalog = [
        ToolCatalogEntry("list_applications", "최근 지원 건 목록", args=("limit",)),
        ToolCatalogEntry("resend_pending_decision", "승인 버튼 재전송", args=("application_id",)),
    ]

    prefix = catalog_prefix(catalog)

    assert "list_applications(limit): 최근 지원 건 목록" in prefix
    assert "resend_pending_decision(application_id): 승인 버튼 재전송" in prefix


def test_catalog_prefix_marks_no_arg_tools():
    prefix = catalog_prefix([ToolCatalogEntry("ping", "생존 확인")])
    assert "ping(): 생존 확인" in prefix


def test_build_prompt_includes_user_text_with_empty_transcript():
    prompt = build_prompt("오늘 지원 몇 건이야?", [])
    assert "오늘 지원 몇 건이야?" in prompt


def test_build_prompt_appends_tool_calls_in_order():
    step1 = AgentStep(action="call_tool", tool="list_applications", tool_args={"limit": "5"})
    step2 = AgentStep(
        action="call_tool", tool="get_application", tool_args={"application_id": "a1"}
    )

    prompt = build_prompt("a1 어떻게 됐어?", [(step1, "3건"), (step2, "AWAITING_APPROVAL")])

    lines = prompt.splitlines()
    assert any("list_applications" in line and "3건" in line for line in lines)
    assert any("get_application" in line and "AWAITING_APPROVAL" in line for line in lines)
    # 순서 보존: list_applications 관찰이 get_application 호출보다 먼저 나와야 한다
    first_idx = next(i for i, line in enumerate(lines) if "list_applications" in line)
    second_idx = next(i for i, line in enumerate(lines) if "get_application" in line)
    assert first_idx < second_idx


def test_build_prompt_reminds_model_to_respond_once_tools_have_run():
    """도구 결과가 있으면 매 턴 종료 조건을 다시 알려준다 — 모델이 성공한 도구를 또 부르는

    습성 때문에 MAX_STEPS 가 소진되던 실측(2026-08-22)에 대한 방어.
    """
    step = AgentStep(action="call_tool", tool="schedule_status", tool_args={})

    assert "respond" not in build_prompt("스케줄 상태", [])
    assert "respond" in build_prompt("스케줄 상태", [(step, "공고 수집: 켜짐")])


def test_fallback_message_apologizes_when_nothing_ran():
    assert "죄송해요" in build_fallback_message([])


def test_fallback_message_reports_tools_that_actually_ran():
    """스텝을 다 써도 도구가 이미 돌았으면 그 결과를 보여준다 — 사과만 하면 사용자가

    "실패했다"고 오해하고 같은 요청을 다시 보낸다(실측 2026-08-22).
    """
    message = build_fallback_message(
        [
            ("set_schedule_time", "자동 지원 스케줄을 14:00 로 바꿨습니다(count=2, updated)."),
            ("set_schedule_enabled", "자동 지원 스케줄을 켰습니다."),
        ]
    )

    assert "죄송해요" not in message
    assert "14:00" in message
    assert "켰습니다" in message
