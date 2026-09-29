"""온보딩 초안 REST contract (T1.3): 업로드 → 추출 → 편집 → 확정, 에러 코드 (§A10 스키마)."""

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from auto_apply.adapters.extract.pdf_docx import PdfDocxTextExtractor
from auto_apply.api.main import create_app
from auto_apply.api.security import TOKEN_HEADER
from auto_apply.config import Settings
from auto_apply.domain.errors import LLMAuthRequired, LLMExecutionError, LLMQuotaExceeded
from auto_apply.domain.unique_identifiers import REDACTION_MARK
from auto_apply.services.profile_drafts import ProfileDraftService
from tests.documents import make_docx
from tests.services.fakes import RecordingLLM

LOCAL = "http://127.0.0.1:8765"
FIXTURES = Path(__file__).parents[1] / "fixtures"
DEV_PDF = (FIXTURES / "resumes" / "dev_resume.pdf").read_bytes()
DEV_PAYLOAD = json.loads((FIXTURES / "resumes" / "dev_extraction.json").read_text("utf-8"))
RRN = "900101-1234567"


@pytest.fixture(params=["memory", "sqlite"])
def client(request, tmp_path) -> Iterator[TestClient]:
    cfg = Settings(
        data_dir=tmp_path / "data",
        storage="memory",
        llm_provider="stub",
        guide_source="static",
        repository=request.param,
    )
    with TestClient(create_app(cfg), base_url=LOCAL) as c:
        c.headers[TOKEN_HEADER] = c.get("/api/session").json()["token"]
        yield c


def _use_llm(client: TestClient, llm) -> None:
    """컨테이너의 초안 서비스만 고정 응답 LLM 으로 바꾼다 — 나머지 배선은 bootstrap 그대로."""
    c = client.app.state.container
    drafts = ProfileDraftService(
        c.uow, c.store, c.uploads, PdfDocxTextExtractor(), llm, c.clock, c.idgen
    )
    client.app.state.container = dataclasses.replace(c, drafts=drafts)


def _upload(client, name="resume.pdf", data=DEV_PDF) -> str:
    res = client.post("/api/documents", files={"file": (name, data, "application/pdf")})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _error(res, status: int, code: str) -> dict:
    assert res.status_code == status, res.text
    assert set(res.json()["error"]) == {"code", "message", "details"}
    assert res.json()["error"]["code"] == code
    return res.json()["error"]


def test_upload_extract_edit_confirm(client):
    _use_llm(client, RecordingLLM(payloads=[DEV_PAYLOAD]))
    doc_id = _upload(client)

    res = client.post("/api/profile/drafts", json={"document_id": doc_id})
    assert res.status_code == 201, res.text
    draft = res.json()
    assert draft["source"] == "resume" and draft["document_id"] == doc_id
    assert draft["content"]["profile"]["name"] == "김가상"
    assert client.get(f"/api/profile/drafts/{draft['id']}").json() == draft

    edited = {**draft["content"], "profile": {**draft["content"]["profile"], "name": "김수정"}}
    res = client.put(f"/api/profile/drafts/{draft['id']}", json=edited)
    assert res.status_code == 200 and res.json()["content"]["profile"]["name"] == "김수정"

    res = client.post(
        f"/api/profile/drafts/{draft['id']}/confirm",
        json={"profile_fields": ["name", "email", "skills"], "experience_indexes": [1]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["profile"]["name"] == "김수정"
    assert client.get("/api/profile").json()["email"] == "gasang.kim@example.com"
    [exp] = client.get("/api/experiences").json()
    assert exp["name"] == "오픈소스 일정 공유 봇" and exp["facts"][0]["id"]
    _error(client.get(f"/api/profile/drafts/{draft['id']}"), 404, "not_found")


def test_v2_import_endpoint(client):
    v2 = FIXTURES / "v2_config"
    res = client.post(
        "/api/profile/drafts/v2-import",
        json={
            "profile_yaml": (v2 / "profile.yaml").read_text("utf-8"),
            "facts_yaml": (v2 / "facts.yaml").read_text("utf-8"),
        },
    )
    assert res.status_code == 201, res.text
    assert res.json()["source"] == "v2_yaml"
    assert len(res.json()["content"]["experiences"]) == 3
    _error(client.post("/api/profile/drafts/v2-import", json={}), 422, "validation_error")
    bad = client.post("/api/profile/drafts/v2-import", json={"facts_yaml": "- [broken"})
    _error(bad, 422, "validation_error")


def test_not_found_and_validation(client):
    _error(client.get("/api/profile/drafts/nope"), 404, "not_found")
    _error(client.delete("/api/profile/drafts/nope"), 404, "not_found")
    _error(client.post("/api/profile/drafts/nope/confirm", json={}), 404, "not_found")
    _error(client.put("/api/profile/drafts/nope", json={}), 404, "not_found")
    _error(client.post("/api/profile/drafts", json={"document_id": "nope"}), 404, "not_found")
    _error(client.post("/api/profile/drafts", json={}), 422, "validation_error")

    draft = client.post(
        "/api/profile/drafts/v2-import", json={"profile_yaml": "name: A\nuser_id: u"}
    )
    draft_id = draft.json()["id"]
    bad_kind = {"experiences": [{"kind": "job", "name": "x"}]}
    _error(client.put(f"/api/profile/drafts/{draft_id}", json=bad_kind), 422, "validation_error")
    res = client.put(f"/api/profile/drafts/{draft_id}", json={"profile": {"phone": RRN}})
    _error(res, 422, "unique_identifier_rejected")
    assert "1234567" not in res.text
    bad_field = {"profile_fields": ["password"]}
    _error(
        client.post(f"/api/profile/drafts/{draft_id}/confirm", json=bad_field),
        422,
        "validation_error",
    )
    out_of_range = {"experience_indexes": [0]}
    _error(
        client.post(f"/api/profile/drafts/{draft_id}/confirm", json=out_of_range),
        422,
        "validation_error",
    )


def test_unreadable_document_is_extraction_failed(client):
    png = client.post(
        "/api/documents", files={"file": ("a.png", b"\x89PNG\r\n\x1a\n....", "image/png")}
    ).json()["id"]
    _error(client.post("/api/profile/drafts", json={"document_id": png}), 422, "extraction_failed")


class _FailingLLM(RecordingLLM):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    async def structured(self, prompt, schema, *, max_tokens=2048, cache_prefix=""):
        raise self.exc


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (LLMQuotaExceeded("quota: 김가상 이력서 원문"), "llm_quota_exceeded"),
        (LLMAuthRequired("login: 김가상 이력서 원문"), "llm_auth_required"),
        (LLMExecutionError("timeout: 김가상 이력서 원문"), "llm_error"),
    ],
)
def test_llm_failures_do_not_echo_output(client, exc, code):
    _use_llm(client, _FailingLLM(exc))
    res = client.post("/api/profile/drafts", json={"document_id": _upload(client)})
    _error(res, 502, code)
    assert "김가상" not in res.text


def test_llm_schema_violation_is_502(client):
    _use_llm(client, RecordingLLM(payloads=[{"profile": {"phone": RRN}}] * 3))
    res = client.post("/api/profile/drafts", json={"document_id": _upload(client)})
    _error(res, 502, "llm_invalid_output")
    assert "1234567" not in res.text


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
RRN_DOCX = make_docx(["김가상", f"주민등록번호 {RRN}", "가상결제 백엔드 엔지니어"])


def test_direct_upload_redacts_and_lists(client):
    """온보딩 업로드: 문서로 저장하지 않고, 번호는 LLM 전에 가리고, 개수만 알린다."""
    llm = RecordingLLM(payloads=[DEV_PAYLOAD])
    _use_llm(client, llm)

    res = client.post(
        "/api/profile/drafts/upload", files={"file": ("이력서.docx", RRN_DOCX, DOCX_TYPE)}
    )

    assert res.status_code == 201, res.text
    draft = res.json()
    assert (draft["redacted_identifiers"], draft["source_filename"]) == (1, "이력서.docx")
    assert draft["document_id"] is None
    assert "1234567" not in res.text
    [prompt] = llm.prompts
    assert "1234567" not in prompt and REDACTION_MARK in prompt
    assert client.get("/api/documents").json() == []  # 온보딩 파일은 문서로 남지 않는다

    [summary] = client.get("/api/profile/drafts").json()
    assert summary == {
        "id": draft["id"],
        "source": "resume",
        "source_filename": "이력서.docx",
        "document_id": None,
        "created_at": draft["created_at"],
        "profile_field_count": 7,  # name·phone·email·links·education·skills·languages
        "experience_count": 2,
        "redacted_identifiers": 1,
    }
    client.post(f"/api/profile/drafts/{draft['id']}/confirm", json={})
    assert client.get("/api/profile/drafts").json() == []


def test_direct_upload_errors(client):
    empty = client.post("/api/profile/drafts/upload", files={"file": ("a.txt", b"x", "text/plain")})
    _error(empty, 415, "unsupported_type")
    png = client.post(
        "/api/profile/drafts/upload", files={"file": ("a.png", b"\x89PNG\r\n\x1a\n..", "image/png")}
    )
    _error(png, 422, "extraction_failed")


def test_document_upload_with_rrn_rejected_without_storing(client):
    """고정 파일은 사용자가 그대로 내는 원본이라 가리지 않고 거부한다 (§A7)."""
    res = client.post("/api/documents", files={"file": ("r.docx", RRN_DOCX, DOCX_TYPE)})
    _error(res, 422, "unique_identifier_rejected")
    assert "1234567" not in res.text
    assert client.get("/api/documents").json() == []
    assert client.app.state.container.store._blobs == {}
