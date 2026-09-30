"""ask_user 도구 — 프로필에 없는 항목을 실행 중에 사람에게 묻는다 (§A5, D10, 절대 규칙 5).

사람은 브라우저가 아니라 웹 UI 에서 답하므로 가드는 켜 둔 채 기다린다(로그인 핸드오프와 다르다).
기다리는 동안 다른 도구 호출은 바로 거부된다. 답은 둘로 갈린다:
- 보통 답 → 답변 KB 에 저장(같은 질문 키면 갱신)하고 값과 `answer_kb` source 를 에이전트에게 준다.
- 가린 답(`sensitive=true` 또는 주민등록번호 꼴) → 저장하지 않고 에이전트에게도 값을 주지 않는다.
  값은 이 run 의 메모리에만 있고, 에이전트는 받은 `user` source(핸들)로 fill·select 를 부른다 —
  앱이 값을 채우고 FillLog 에는 `withheld` 로 남는다. 그래서 DB·로그·transcript 어디에도 없다.
시간 안에 답이 없으면 run 을 NEEDS_INPUT 으로 끝낸다 — 답이 오면 재진입 run 이 이어간다(D8).
"""

import uuid
from collections.abc import Mapping
from types import MappingProxyType

from auto_apply.contracts.browser_tools import AskUserInput, ToolError, ToolResult, UserAnswer
from auto_apply.contracts.fill_log import FillSource, FillSourceKind
from auto_apply.contracts.human_gate import HumanOutcome, HumanReply, HumanTask, HumanTaskKind
from auto_apply.domain.errors import InvalidInput
from auto_apply.domain.question_key import normalize_question_key
from auto_apply.domain.unique_identifiers import contains_resident_registration_number
from auto_apply.services.browser_toolbox_base import Refused, ToolboxBase
from auto_apply.services.browser_toolbox_record import fail
from auto_apply.services.browser_toolbox_redact import redact_text

_TIMED_OUT = "사람이 제한 시간 안에 답하지 않았다 — run 을 끝낸다(답이 오면 이어서 다시 연다)"
_DECLINED = "사람이 답하지 않겠다고 했다 — 이 칸 없이 진행하거나, 필수면 report_failure 로 끝낸다"
_HIDDEN = (
    "답을 받았지만 민감한 값이라 보이지 않는다. 이 source 를 그대로 넣어 fill(value 는 빈 문자열)"
    " 또는 select(option 은 빈 문자열)를 부르면 앱이 값을 채운다."
)
_SHOWN = "답을 받았다. 이 값을 넣을 때 source 를 그대로 쓴다."


class AskTools(ToolboxBase):
    @property
    def hidden_answers(self) -> Mapping[str, str]:
        """이 run 이 쥔 가린 답(핸들 → 값) — 재진입 run 에 메모리로만 넘긴다. 기록·로그 금지."""
        return MappingProxyType(dict(self._private))

    async def _ask_user(self, data: AskUserInput) -> ToolResult:
        try:
            normalize_question_key(data.question)
        except InvalidInput as e:
            raise Refused(ToolError.INVALID_INPUT, "질문에 글자가 없다") from e
        url = self._snapshot.url if self._snapshot is not None else ""
        task = HumanTask(
            id=uuid.uuid4().hex, kind=HumanTaskKind.QUESTION, application_id=self._application_id,
            run_id=self._run_id, question=redact_text(data.question),
            field_hint=redact_text(data.field_hint), page_url=redact_text(url)[:2048],
            options=tuple(redact_text(o) for o in data.options or ()), sensitive=data.sensitive,
        )  # fmt: skip
        self._awaiting = task  # 기다리는 동안 다른 도구는 거부 — 가드는 켜진 채다
        try:
            self._log.info("toolbox.ask_user", task_id=task.id, sensitive=task.sensitive)
            reply = await self._gate.wait(task, timeout_s=self._human_wait_s)
        finally:
            self._awaiting = None
        self._log.info("toolbox.ask_user_done", task_id=task.id, outcome=reply.outcome.value)
        if reply.outcome is HumanOutcome.TIMED_OUT:
            self._needs_human = task
            return fail("ask_user", ToolError.NEEDS_INPUT, _TIMED_OUT)
        if reply.outcome is HumanOutcome.DECLINED or not reply.answer.strip():
            return fail("ask_user", ToolError.ANSWER_DECLINED, _DECLINED)
        answer = await self._keep_answer(task, reply)
        message = _SHOWN if answer.value is not None else _HIDDEN
        return ToolResult(tool="ask_user", ok=True, answer=answer, message=message)

    async def _keep_answer(self, task: HumanTask, reply: HumanReply) -> UserAnswer:
        value = reply.answer
        if task.sensitive or contains_resident_registration_number(value):
            self._private[task.id] = value  # 이 run 의 메모리에만 (절대 규칙 5)
            return UserAnswer(source=FillSource(kind=FillSourceKind.USER, key=task.id))
        if self._answers is not None:
            try:
                row = await self._answers.remember_answer(
                    self._user_id, task.question, value, application_id=self._application_id
                )
            except Exception as e:  # 저장이 안 돼도 사람이 준 답은 이 run 에서 쓴다
                self._log.warning("toolbox.answer_kb_failed", error=type(e).__name__)
            else:
                return UserAnswer(
                    source=FillSource(kind=FillSourceKind.ANSWER_KB, key=row.id), value=value
                )
        return UserAnswer(source=FillSource(kind=FillSourceKind.USER, key=task.id), value=value)
