"""domain/chat_agent.py — 프롬프트 조립 순수 함수(포트 무의존)."""

from auto_apply.ai.schemas import AgentStep
from auto_apply.domain.chat_agent import ToolCatalogEntry, build_prompt, catalog_prefix


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
