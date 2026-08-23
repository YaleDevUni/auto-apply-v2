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

    prompt = build_prompt(
        "a1 어떻게 됐어?", [(step1, "3건", "done"), (step2, "AWAITING_APPROVAL", "done")]
    )

    lines = prompt.splitlines()
    first_idx = next(i for i, line in enumerate(lines) if "list_applications" in line)
    second_idx = next(i for i, line in enumerate(lines) if "get_application" in line)
    # 관찰 결과는 그 호출 바로 다음 줄에 붙는다
    assert "3건" in lines[first_idx + 1]
    assert "AWAITING_APPROVAL" in lines[second_idx + 1]
    # 순서 보존: list_applications 가 get_application 보다 먼저 나와야 한다
    assert first_idx < second_idx


def test_build_prompt_marks_finished_calls_and_restates_the_exit_condition():
    """지난 호출을 "이미 완료"로 표시하고 매 턴 종료 조건을 다시 알려준다.

    로그 한 줄(`[도구 호출] x -> y`) 형식이면 모델이 같은 도구를 다시 부른다는 걸 실측
    (2026-08-22, 복합 요청 3건에서 매번 중복 3~4회)하고 바꾼 포맷이다.
    """
    step = AgentStep(action="call_tool", tool="schedule_status", tool_args={})

    empty = build_prompt("스케줄 상태", [])
    assert "respond" not in empty
    assert "아직 아무 도구도 실행하지 않았습니다" in empty

    after = build_prompt("스케줄 상태", [(step, "공고 수집: 켜짐", "done")])
    assert "다시 부르면 무시됩니다" in after
    assert "respond" in after


def test_build_prompt_marks_failed_calls_differently_from_done():
    """/code-review finding #3 회귀: 실패/차단을 ✅ 로 뭉뚱그리면 모델이 실패도 성공으로 읽는다.

    실제 사고 시나리오: start_applications 가 예외로 실패했는데 이전 코드는 ✅ 로 표시해서
    모델이 "지원을 시작했습니다"로 답했다.
    """
    ok_step = AgentStep(action="call_tool", tool="start_applications", tool_args={"count": "2"})
    failed_step = AgentStep(action="call_tool", tool="apply_by_url", tool_args={"url": "u"})
    blocked_step = AgentStep(
        action="call_tool", tool="start_applications", tool_args={"count": "2"}
    )

    prompt = build_prompt(
        "지원 2건 시작해줘",
        [
            (ok_step, "2건 시작함", "done"),
            (failed_step, "apply_by_url 실행 중 오류가 발생했습니다: boom", "failed"),
            (blocked_step, "이미 같은 인자로 호출했습니다", "blocked"),
        ],
    )

    lines = dict(enumerate(prompt.splitlines()))
    done_idx = next(i for i, line in lines.items() if "start_applications" in line and i < 10)
    failed_idx = next(i for i, line in lines.items() if "apply_by_url" in line)
    blocked_idx = next(
        i for i, line in lines.items() if "start_applications" in line and i > failed_idx
    )
    assert "✅" in lines[done_idx]
    assert "⚠️" in lines[failed_idx] and "✅" not in lines[failed_idx]
    assert "🔁" in lines[blocked_idx] and "✅" not in lines[blocked_idx]


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
