"""도구 표면(§A4 L1·§A5) — 무엇이 있고, 무엇이 **없는지**, 입력이 어떻게 검증되는지."""

import json

import pytest
from pydantic import ValidationError

from auto_apply.contracts.browser_tools import (
    FillInput,
    NavigateInput,
    ToolError,
    UploadInput,
    WaitForInput,
)
from auto_apply.contracts.fill_log import FillSource
from auto_apply.domain.errors import PageFailure
from auto_apply.services.browser_toolbox_specs import TOOLS

EXPECTED = {
    "snapshot",
    "navigate",
    "back",
    "scroll",
    "wait_for",
    "fill",
    "select",
    "check",
    "upload",
    "report_failure",
}
# 이름에 이 조각이 들어간 도구는 없어야 한다 — 임의 JS·키 입력·좌표 클릭·파일 경로, 그리고
# 하네스(T2.5) 없이 붙는 click·제출.
FORBIDDEN_TOOL_WORDS = (
    "click", "eval", "script", "js", "execute", "press", "key", "type", "mouse", "tap",
    "coord", "submit", "file_path", "path", "dialog", "cookie", "download", "tab",
)  # fmt: skip
# 어느 도구 입력에도 이런 인자는 없어야 한다. `key` 는 키 입력 인자로서만 금지 — 도구 입력 최상위에
# 없어야 하고, 중첩된 FillSource.key(근거 id)는 괜찮다.
FORBIDDEN_ARGS = {
    "script", "js", "code", "expression", "function", "keys", "keyboard", "press", "x", "y",
    "selector", "css", "xpath", "path", "file", "file_path", "filename", "headers", "cookie",
}  # fmt: skip
GOOD_SOURCE = {"kind": "profile", "key": "email"}


def test_tool_names_are_exactly_the_spec():
    assert set(TOOLS) == EXPECTED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_no_forbidden_tool(name):
    assert not [w for w in FORBIDDEN_TOOL_WORDS if w in name]


def _properties(schema: dict) -> set[str]:
    found = set(schema.get("properties", {}))
    for sub in schema.get("$defs", {}).values():
        found |= set(sub.get("properties", {}))
    return found


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_tool_inputs_are_closed_and_have_no_forbidden_args(name):
    schema = TOOLS[name].input_model.model_json_schema()
    assert schema.get("additionalProperties") is False
    assert not (_properties(schema) & FORBIDDEN_ARGS)
    assert "key" not in schema.get("properties", {})
    json.dumps(schema)  # M3 가 그대로 노출할 수 있는 JSON 이다
    with pytest.raises(ValidationError):
        TOOLS[name].input_model.model_validate({"script": "document.forms[0].submit()"})


@pytest.mark.parametrize(
    "ref", ["", "e0", "e01", "E1", "1", "#submit", "css=button", "e1 ", "e1;e2", "e12345678"]
)
def test_ref_shape(ref):
    with pytest.raises(ValidationError):
        FillInput(ref=ref, value="v", source=FillSource.model_validate(GOOD_SOURCE))


def test_good_ref():
    assert (
        FillInput.model_validate({"ref": "e42", "value": "v", "source": GOOD_SOURCE}).ref == "e42"
    )


@pytest.mark.parametrize(
    "source",
    [
        {},
        {"kind": "guess"},
        {"kind": "profile"},  # 어느 필드인지 없으면 근거가 아니다
        {"kind": "fact"},
        {"kind": "answer_kb"},
        {"kind": "fact", "key": "../etc/passwd"},
        {"kind": "fact", "key": "a b"},
        {"kind": "user", "key": "x", "extra": 1},
    ],
)
def test_source_is_required_and_validated(source):
    with pytest.raises(ValidationError):
        FillInput.model_validate({"ref": "e1", "value": "v", "source": source})
    with pytest.raises(ValidationError):
        FillInput.model_validate({"ref": "e1", "value": "v"})


@pytest.mark.parametrize(
    "source", [{"kind": "generated"}, {"kind": "user"}, {"kind": "fact", "key": "fact_1a2b"}]
)
def test_sources_accepted(source):
    FillInput.model_validate({"ref": "e1", "value": "v", "source": source})


@pytest.mark.parametrize(
    "url",
    [
        "javascript:document.forms[0].submit()",
        "JavaScript:alert(1)",
        " javascript:alert(1)",
        "data:text/html,<form>",
        "file:///etc/passwd",
        "chrome://settings",
        "view-source:http://example.com",
        "about:blank",
        "http://",
        "//example.com/x",
    ],
)
def test_navigate_web_urls_only(url):
    with pytest.raises(ValidationError):
        NavigateInput(url=url)


@pytest.mark.parametrize(
    "doc", ["/etc/passwd", "../doc_1", "C:\\Users\\me\\a.pdf", "doc 1", "", "a" * 65]
)
def test_upload_takes_document_ids_not_paths(doc):
    with pytest.raises(ValidationError):
        UploadInput(ref="e1", document_id=doc)


@pytest.mark.parametrize("args", [{}, {"text": "a", "ms": 5}, {"ms": 0}, {"ms": 10_001}])
def test_wait_for_needs_exactly_one_bounded_arg(args):
    with pytest.raises(ValidationError):
        WaitForInput.model_validate(args)


def test_validation_errors_do_not_echo_input():
    secret = "900101-1234567"
    with pytest.raises(ValidationError) as e:
        FillInput.model_validate({"ref": secret, "value": "v", "source": GOOD_SOURCE})
    assert secret not in str(e.value)


def test_every_page_failure_maps_to_a_tool_error():
    assert {f.value for f in PageFailure} <= {e.value for e in ToolError}
