"""AgentStep — 텔레그램 채팅 에이전트 ReAct 루프 한 스텝의 shape 검증 (telegram/agent.py)."""

import pytest
from pydantic import ValidationError

from auto_apply.ai.schemas import AgentStep


def test_call_tool_without_tool_name_is_rejected():
    with pytest.raises(ValidationError, match="tool"):
        AgentStep.model_validate({"action": "call_tool"})


def test_respond_without_response_text_is_rejected():
    with pytest.raises(ValidationError, match="response"):
        AgentStep.model_validate({"action": "respond"})


def test_valid_call_tool_round_trips():
    step = AgentStep.model_validate(
        {"action": "call_tool", "tool": "list_applications", "tool_args": {"limit": "5"}}
    )
    assert step.tool == "list_applications"
    assert step.tool_args == {"limit": "5"}


def test_valid_respond_round_trips():
    step = AgentStep.model_validate({"action": "respond", "response": "오늘 지원한 건 3건이에요."})
    assert step.response == "오늘 지원한 건 3건이에요."


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        AgentStep.model_validate({"action": "respond", "response": "ok", "extra": "nope"})


def test_unknown_action_literal_is_rejected():
    with pytest.raises(ValidationError):
        AgentStep.model_validate({"action": "do_whatever", "response": "ok"})
