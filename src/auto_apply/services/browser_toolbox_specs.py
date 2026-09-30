"""BrowserToolbox 도구 목록 — 에이전트가 쓸 수 있는 동작의 **유일한** 정의 (§A5, §A4 L1).

M3 의 두 런타임(API in-process · CLI MCP)이 이 표를 그대로 노출한다. 여기 없는 동작은 에이전트에게
존재하지 않는다: 임의 JS·키 입력(Enter)·좌표 클릭·파일 경로·하네스 모드 전환·제출은 없다. `click` 은
SubmitGuard(§A4 L2·L3) 창 안에서만 돈다. 도구를 더할 때는
tests/services/test_browser_toolbox_schema.py 의 금지 목록을 먼저 본다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from auto_apply.contracts.browser_tools import (
    BackInput,
    CheckInput,
    ClickInput,
    FillInput,
    NavigateInput,
    ReadyForReviewInput,
    ReportFailureInput,
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
        " 비밀번호·인증 코드 칸은 secret 으로 표시되고 값이 없다.",
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
        "ref 요소를 누른다. 제출처럼 보이는 요소는 하네스가 제출 요청을 막는다 —"
        " 막히면 submit_blocked 가 오고, 그때는 ready_for_review 로 넘긴다"
        "(최종 제출이든 단계 이동이든 같다)."
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
        "ready_for_review",
        "입력을 마쳤다. submit_ref 는 사람이 승인하면 하네스가 누를 제출(또는 다음 단계) 버튼이다."
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
