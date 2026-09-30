"""BrowserToolbox 도구 목록 — 에이전트가 쓸 수 있는 동작의 **유일한** 정의 (§A5, §A4 L1).

M3 의 두 런타임(API in-process · CLI MCP)이 이 표를 그대로 노출한다. 여기 없는 동작은 에이전트에게
존재하지 않는다: 임의 JS·키 입력(Enter)·좌표 클릭·파일 경로·하네스 모드 전환·제출은 없다. `click` 은
SubmitGuard(§A4 L2·L3) 창 안에서만 돈다. 도구를 더할 때는
tests/services/test_browser_toolbox_schema.py 의 금지 목록을 먼저 본다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from auto_apply.contracts.agent import AgentTool
from auto_apply.contracts.browser_tools import (
    AskUserInput,
    BackInput,
    CheckInput,
    ClickInput,
    FillInput,
    NavigateInput,
    ReadyForReviewInput,
    ReportFailureInput,
    RequestHumanInput,
    RequestLoginInput,
    ScrollInput,
    SelectInput,
    SnapshotInput,
    ToolInput,
    UploadInput,
    WaitForInput,
)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]


_SOURCE = (
    " source 는 값의 근거다: {kind: profile|fact|answer_kb, key: 필드/fact id/답변 id} 또는"
    " {kind: generated|user}. 기록은 승인 화면에 그대로 나온다."
)

_SPECS = (
    ToolSpec(
        "snapshot",
        "현재 탭(모든 프레임)의 접근성 트리. 조작할 수 있는 요소에 ref(e1, e2 …)가 붙는다."
        " ref 는 가장 최근 snapshot 것만 유효하다 — 이동·페이지 변화 뒤에는 다시 부른다."
        " 비밀번호·인증 코드 칸은 secret 으로 표시되고 값이 없다."
        " 로그인 화면·CAPTCHA·인증 코드 화면으로 보이면 handoff 에 근거가 붙는다.",
        SnapshotInput,
    ),
    ToolSpec("navigate", "http(s) 주소로 이동한다.", NavigateInput),
    ToolSpec("back", "브라우저 뒤로 가기.", BackInput),
    ToolSpec("scroll", "ref 요소가 보이게, 또는 ref 없이 한 화면 위/아래로 스크롤.", ScrollInput),
    ToolSpec(
        "wait_for",
        "text 가 화면에 나타날 때까지(최대 10초) 또는 ms 만큼 기다린다. 둘 중 하나만 준다.",
        WaitForInput,
    ),
    ToolSpec(
        "click",
        "ref 요소를 누른다. '다음'·Next 같은 단계 이동 버튼은 하네스가 확인하고 통과시킨다."
        " 제출처럼 보이는 요소는 하네스가 제출 요청을 막는다 —"
        " 막히면 submit_blocked 가 오고, 그때는 ready_for_review 로 넘긴다."
        " 지원 완료 화면이 나타나면 run 이 멈춘다.",
        ClickInput,
    ),
    ToolSpec(
        "fill",
        "글자 칸(input·textarea)의 값을 바꾼다. 키 입력이 아니라 Enter 가 눌리지 않는다."
        " 비밀번호·인증 코드 칸은 거부된다 — 로그인은 사람이 한다." + _SOURCE,
        FillInput,
    ),
    ToolSpec("select", "select 에서 선택지(라벨)를 고른다." + _SOURCE, SelectInput),
    ToolSpec("check", "checkbox 를 켜고 끄거나 radio 를 고른다." + _SOURCE, CheckInput),
    ToolSpec(
        "upload",
        "파일 입력에 앱에 등록된 문서(document_id)를 넣는다. 파일 경로는 받지 않는다.",
        UploadInput,
    ),
    ToolSpec(
        "request_login",
        "로그인이 필요할 때 사람에게 넘긴다. 사람이 전용 크롬 창에서 직접 로그인한다"
        " — 앱은 비밀번호를 입력하지 않고 계정을 만들지 않는다. 사람이 끝낼 때까지 다른 도구는"
        " awaiting_human 으로 거부된다. 끝나면 화면이 바뀌었을 수 있으니 snapshot 부터 다시 본다."
        " 사람이 제때 끝내지 못하면 needs_login 으로 run 이 끝난다."
        " site 는 사람에게 보일 사이트 이름.",
        RequestLoginInput,
    ),
    ToolSpec(
        "request_human",
        "CAPTCHA·SMS·본인인증처럼 사람만 할 수 있는 일을 부탁한다(앱은 풀거나 우회하지 않는다)."
        " 기다리는 동안 다른 도구는 거부되고, 끝나면 snapshot 부터 다시 본다."
        " 사람이 제때 끝내지 못하면 needs_input 으로 run 이 끝난다. reason 은 사람에게 보인다.",
        RequestHumanInput,
    ),
    ToolSpec(
        "ask_user",
        "프로필·답변 KB 에 없는 값이 필요할 때 사람에게 묻는다. 사람이 웹 화면에서 답할 때까지"
        " 기다리고, 그동안 다른 도구는 awaiting_human 으로 거부된다."
        " 답은 answer 로 온다 — 넣을 때 answer.source 를 그대로 쓴다. answer.value 가 없으면"
        ' 민감한 답이라 앱만 값을 쥔다: fill(value="")·select(option="") 에'
        " 그 source 를 넣으면 앱이 채운다."
        " sensitive=true 면 답을 저장하지 않는다(건강·가족처럼 민감한 문항)."
        " 사람이 제때 답하지 않으면 needs_input 으로 run 이 끝나고, 답이 오면 다시 이어서 연다."
        " 주민등록번호는 묻지 않는다.",
        AskUserInput,
    ),
    ToolSpec(
        "ready_for_review",
        "입력을 마쳤다. submit_ref 는 사람이 승인하면 하네스가 누를 최종 제출 버튼이다."
        " 지금까지의 입력 기록과 함께 승인 대기로 넘기고 run 을 끝낸다."
        " 에이전트는 제출하지 않는다.",
        ReadyForReviewInput,
    ),
    ToolSpec(
        "report_failure",
        "더 진행할 수 없을 때 이유와 함께 run 을 끝낸다. 그 뒤에는 어떤 도구도 받지 않는다.",
        ReportFailureInput,
    ),
)

TOOLS: Mapping[str, ToolSpec] = MappingProxyType({s.name: s for s in _SPECS})


def agent_tools() -> tuple[AgentTool, ...]:
    """런타임에 넘길 도구 목록 (§A6) — 입력 모델의 JSON Schema 가 곧 도구 스키마다."""
    return tuple(
        AgentTool(
            name=s.name, description=s.description, input_schema=s.input_model.model_json_schema()
        )
        for s in _SPECS
    )
