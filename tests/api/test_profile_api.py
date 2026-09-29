"""프로필·경험·답변·문서 REST contract (성공·검증 실패·404) + 일관된 에러 스키마 (T1.2).

sqlite·memory 두 repository 로 같은 기대를 건다 — API 는 저장소 구현을 몰라야 한다.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from auto_apply.api.main import create_app
from auto_apply.api.schemas import ProfileBody
from auto_apply.api.security import TOKEN_HEADER
from auto_apply.config import Settings
from auto_apply.contracts.profile import Profile

LOCAL = "http://127.0.0.1:8765"
RRN = "900101-1234567"
PDF = b"%PDF-1.7\n" + bytes(range(256)) * 4 + b"\r\n--\r\n\r\r\n\n"
MAX_BYTES = 4096


@pytest.fixture(params=["memory", "sqlite"])
def client(request, tmp_path) -> Iterator[TestClient]:
    cfg = Settings(
        data_dir=tmp_path / "data",
        storage="memory",
        llm_provider="stub",
        guide_source="static",
        repository=request.param,
        document_max_bytes=MAX_BYTES,
    )
    with TestClient(create_app(cfg), base_url=LOCAL) as c:
        c.headers[TOKEN_HEADER] = c.get("/api/session").json()["token"]
        yield c


def _assert_error(res, status: int, code: str) -> dict:
    assert res.status_code == status, res.text
    body = res.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    return body["error"]


# ── 인적사항 ─────────────────────────────────────────────────────────────────


def test_profile_body_mirrors_profile_without_user_id():
    assert set(ProfileBody.model_fields) == set(Profile.model_fields) - {"user_id"}


def test_profile_put_get(client):
    _assert_error(client.get("/api/profile"), 404, "not_found")
    body = {
        "name": "홍길동",
        "email": "a@example.com",
        "links": [{"label": "GitHub", "url": "https://github.com/x"}],
        "additional": {"military": {"status": "exempt"}, "veteran": False},
    }
    res = client.put("/api/profile", json=body)
    assert res.status_code == 200, res.text
    got = client.get("/api/profile").json()
    assert got["name"] == "홍길동"
    assert got["additional"]["military"]["status"] == "exempt"
    assert got["additional"]["desired_salary"] is None  # 비워둠 = 실행 중 질문 (D10)


def test_profile_validation_errors(client):
    err = _assert_error(client.put("/api/profile", json={"phone": "010"}), 422, "validation_error")
    assert err["details"][0]["loc"][-1] == "name"
    _assert_error(client.put("/api/profile", json={"name": " "}), 422, "validation_error")
    _assert_error(
        client.put("/api/profile", json={"name": "a", "additional": {"military": {"status": "x"}}}),
        422,
        "validation_error",
    )


def test_profile_rrn_rejected_without_echo(client):
    res = client.put("/api/profile", json={"name": "홍길동", "phone": RRN})
    _assert_error(res, 422, "unique_identifier_rejected")
    assert "1234567" not in res.text
    _assert_error(client.get("/api/profile"), 404, "not_found")


# ── 경험 ─────────────────────────────────────────────────────────────────────


def _exp(**kw) -> dict:
    return {"kind": "project", "name": "가상 프로젝트", "facts": [{"text": "응답 30% 단축"}], **kw}


def test_experience_crud(client):
    assert client.get("/api/experiences").json() == []
    res = client.post("/api/experiences", json=_exp())
    assert res.status_code == 201, res.text
    exp = res.json()
    assert exp["id"] and exp["facts"][0]["id"]

    assert client.get(f"/api/experiences/{exp['id']}").json() == exp
    upd = client.put(
        f"/api/experiences/{exp['id']}",
        json=_exp(name="이름 변경", facts=[{"id": exp["facts"][0]["id"], "text": "고침"}]),
    )
    assert upd.status_code == 200
    assert upd.json()["facts"][0]["id"] == exp["facts"][0]["id"]
    assert [e["name"] for e in client.get("/api/experiences").json()] == ["이름 변경"]

    assert client.delete(f"/api/experiences/{exp['id']}").status_code == 204
    _assert_error(client.get(f"/api/experiences/{exp['id']}"), 404, "not_found")
    _assert_error(client.delete(f"/api/experiences/{exp['id']}"), 404, "not_found")
    _assert_error(client.put("/api/experiences/nope", json=_exp()), 404, "not_found")


def test_experience_validation(client):
    _assert_error(client.post("/api/experiences", json=_exp(kind="job")), 422, "validation_error")
    _assert_error(
        client.post("/api/experiences", json=_exp(document_ids=["missing"])),
        422,
        "validation_error",
    )
    first = client.post("/api/experiences", json=_exp(facts=[{"id": "f1", "text": "a"}]))
    assert first.status_code == 201
    _assert_error(
        client.post("/api/experiences", json=_exp(facts=[{"id": "f1", "text": "b"}])),
        422,
        "validation_error",
    )
    res = client.post("/api/experiences", json=_exp(role=RRN))
    _assert_error(res, 422, "unique_identifier_rejected")
    assert RRN not in res.text


# ── 답변KB ───────────────────────────────────────────────────────────────────


def test_answer_crud(client):
    res = client.post("/api/answers", json={"question": "희망 연봉은?*", "answer": "내규"})
    assert res.status_code == 201, res.text
    ans = res.json()
    assert ans["question_key"] == "희망 연봉은"
    assert client.get(f"/api/answers/{ans['id']}").json() == ans

    upd = client.put(f"/api/answers/{ans['id']}", json={"question": "희망 연봉", "answer": "협의"})
    assert upd.status_code == 200
    assert [a["answer"] for a in client.get("/api/answers").json()] == ["협의"]

    assert client.delete(f"/api/answers/{ans['id']}").status_code == 204
    _assert_error(client.get(f"/api/answers/{ans['id']}"), 404, "not_found")


def test_answer_errors(client):
    _assert_error(client.post("/api/answers", json={"question": "q"}), 422, "validation_error")
    _assert_error(
        client.post("/api/answers", json={"question": " ?* ", "answer": "a"}),
        422,
        "validation_error",
    )
    assert client.post(
        "/api/answers", json={"question": "입사 가능일", "answer": "즉시"}
    ).is_success
    _assert_error(
        client.post("/api/answers", json={"question": "입사 가능일?", "answer": "다음 달"}),
        409,
        "conflict",
    )
    _assert_error(
        client.post("/api/answers", json={"question": "주민번호", "answer": RRN}),
        422,
        "unique_identifier_rejected",
    )
    _assert_error(
        client.put("/api/answers/nope", json={"question": "q", "answer": "a"}), 404, "not_found"
    )


# ── 문서 ─────────────────────────────────────────────────────────────────────


def _upload(client, name: str, data: bytes, ctype: str = "application/pdf"):
    return client.post("/api/documents", files={"file": (name, data, ctype)})


def test_document_upload_list_download_delete(client):
    res = _upload(client, "이력서 최종.pdf", PDF)
    assert res.status_code == 201, res.text
    doc = res.json()
    assert doc["filename"] == "이력서 최종.pdf"
    assert doc["content_type"] == "application/pdf"
    assert doc["size_bytes"] == len(PDF)

    assert client.get("/api/documents").json() == [doc]
    assert client.get(f"/api/documents/{doc['id']}").json() == doc
    content = client.get(f"/api/documents/{doc['id']}/content")
    assert content.status_code == 200
    assert content.content == PDF
    assert content.headers["content-type"] == "application/pdf"
    assert content.headers["x-content-type-options"] == "nosniff"

    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    _assert_error(client.get(f"/api/documents/{doc['id']}"), 404, "not_found")
    _assert_error(client.get(f"/api/documents/{doc['id']}/content"), 404, "not_found")


def test_document_content_type_is_decided_by_server(client):
    """클라이언트가 보낸 content type 은 믿지 않는다 — 확장자·바이트로 정한다."""
    res = _upload(client, "a.pdf", PDF, ctype="text/html")
    assert res.status_code == 201
    assert res.json()["content_type"] == "application/pdf"


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("a.exe", b"MZ\x90\x00"),
        ("a.html", b"<html></html>"),
        ("a.svg", b"<svg/>"),
        ("fake.pdf", b"<html><script>alert(1)</script>"),
        ("fake.jpg", PDF),
    ],
)
def test_document_type_rejected(client, name, data):
    _assert_error(_upload(client, name, data), 415, "unsupported_type")
    assert client.get("/api/documents").json() == []


def test_document_size_rejected(client):
    _assert_error(_upload(client, "big.pdf", b"%PDF-" + b"0" * MAX_BYTES), 413, "too_large")
    # 본문 자체가 상한 + 여유분을 넘으면 파싱 전에 끊는다
    huge = b"%PDF-" + b"0" * (MAX_BYTES + 128 * 1024)
    _assert_error(_upload(client, "huge.pdf", huge), 413, "too_large")
    assert client.get("/api/documents").json() == []


def test_document_upload_malformed(client):
    _assert_error(_upload(client, "empty.pdf", b""), 422, "empty")
    _assert_error(
        client.post("/api/documents", files={"other": ("a.pdf", PDF, "application/pdf")}),
        422,
        "validation_error",
    )
    _assert_error(
        client.post("/api/documents", content=PDF, headers={"Content-Type": "application/pdf"}),
        415,
        "unsupported_type",
    )
    res = _upload(client, f"{RRN}.pdf", PDF)
    _assert_error(res, 422, "unique_identifier_rejected")
    assert RRN not in res.text


def test_unknown_route_uses_error_schema(client):
    _assert_error(client.get("/api/nope"), 404, "not_found")
    _assert_error(client.patch("/api/profile", json={}), 405, "method_not_allowed")


def test_document_size_cap_applies_to_chunked_body(client):
    """Content-Length 없이 흘려보내는 본문도 상한에서 끊는다 — 메모리를 채우지 못하게."""
    boundary = "xBOUNDARYx"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n%PDF-"
    ).encode()

    def chunks():
        yield head
        for _ in range(200):
            yield b"0" * 1024
        yield f"\r\n--{boundary}--\r\n".encode()

    res = client.post(
        "/api/documents",
        content=chunks(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert "content-length" not in {k.lower() for k in res.request.headers}
    _assert_error(res, 413, "too_large")


# ── multipart 회귀 (T1.2 검증에서 실측된 결함 ①~⑤) ─────────────────────────────

BOUNDARY = "xBOUNDARYx"
MULTIPART = {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}


def _part(name: str, body: bytes, filename: str | None = None) -> bytes:
    disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
    return f"--{BOUNDARY}\r\nContent-Disposition: {disp}\r\n\r\n".encode() + body + b"\r\n"


def _close() -> bytes:
    return f"--{BOUNDARY}--\r\n".encode()


def test_truncated_body_without_closing_boundary_rejected(client):
    """① 닫는 경계가 없는(잘린) 본문을 정상 업로드로 저장하지 않는다."""
    body = _part("file", PDF, "a.pdf")  # 닫는 경계 없음
    _assert_error(
        client.post("/api/documents", content=body, headers=MULTIPART), 422, "validation_error"
    )
    cut = (_part("file", PDF, "a.pdf") + _close())[: len(PDF) // 2]
    _assert_error(
        client.post("/api/documents", content=cut, headers=MULTIPART), 422, "validation_error"
    )
    assert client.get("/api/documents").json() == []


def test_duplicate_file_parts_rejected(client):
    """② file 파트가 둘이면 조용히 첫 것을 쓰지 않고 거부한다."""
    body = _part("file", PDF, "a.pdf") + _part("file", PDF, "b.pdf") + _close()
    _assert_error(
        client.post("/api/documents", content=body, headers=MULTIPART), 422, "validation_error"
    )
    assert client.get("/api/documents").json() == []


def test_missing_boundary_rejected(client):
    res = client.post(
        "/api/documents",
        content=_part("file", PDF, "a.pdf") + _close(),
        headers={"Content-Type": "multipart/form-data"},
    )
    _assert_error(res, 422, "validation_error")


def test_backslash_path_filename_reduced_to_basename(client):
    """③ `..\\..\\x.pdf` 는 `x.pdf` 로 (예전 구현은 `....x.pdf`)."""
    body = _part("file", PDF, "..\\..\\x.pdf") + _close()
    res = client.post("/api/documents", content=body, headers=MULTIPART)
    assert res.status_code == 201, res.text
    assert res.json()["filename"] == "x.pdf"


def test_filename_control_chars_and_length_sanitized(client):
    """④ NUL 등 제어문자는 지우고, 긴 파일명은 255자로 자른다(확장자 유지). 60KB 는 거부."""
    res = client.post(
        "/api/documents", content=_part("file", PDF, "a\x00b.pdf") + _close(), headers=MULTIPART
    )
    assert res.status_code == 201, res.text
    assert res.json()["filename"] == "ab.pdf"

    body = _part("file", PDF, "가" * 1_000 + ".pdf") + _close()
    res = client.post("/api/documents", content=body, headers=MULTIPART)
    assert res.status_code == 201, res.text
    name = res.json()["filename"]
    assert len(name) == 255 and name.endswith(".pdf")

    huge_name = "가" * 20_000 + ".pdf"  # UTF-8 로 60KB — 파트 헤더 상한에서 거부
    body = _part("file", PDF, huge_name) + _close()
    _assert_error(
        client.post("/api/documents", content=body, headers=MULTIPART), 422, "validation_error"
    )
    assert len(client.get("/api/documents").json()) == 2


def test_many_small_parts_rejected_fast(client):
    """⑤ 작은 파트를 잔뜩 보내 파서를 붙잡는 요청은 초반에 끊는다."""
    import time

    body = b"".join(_part(f"f{i}", b"x") for i in range(500)) + _part("file", PDF, "a.pdf")
    started = time.monotonic()
    res = client.post("/api/documents", content=body + _close(), headers=MULTIPART)
    _assert_error(res, 422, "validation_error")
    assert time.monotonic() - started < 2
    # 20만 개처럼 본문이 상한을 넘으면 파싱 전에 413
    huge = b"".join(_part(f"f{i}", b"x") for i in range(200_000))
    _assert_error(client.post("/api/documents", content=huge, headers=MULTIPART), 413, "too_large")


async def test_upload_memory_peak_is_about_one_file(tmp_path):
    """⑤ 본문 전체를 메모리에 올리지 않는다 (예전 구현: 10MB 업로드에 peak 105MB).

    TestClient 는 요청 본문을 한 덩어리로 만들어 보내므로, 스트리밍하는 ASGITransport 로
    직접 부른다.
    남는 것은 서비스에 넘기는 파일 바이트 한 벌(≈1x)뿐이어야 한다.
    """
    import tracemalloc

    import httpx

    from auto_apply.bootstrap import build_container

    size = 8 * 1024 * 1024
    cfg = Settings(
        data_dir=tmp_path / "data",
        storage="local",
        repository="memory",
        llm_provider="stub",
        guide_source="static",
        document_max_bytes=10 * 1024 * 1024,
    )
    app = create_app(cfg, prepare=False)
    app.state.container = build_container(cfg)

    async def body():
        yield _part("file", b"", "a.pdf")[:-2] + b"%PDF-"
        chunk = b"0" * 65536
        for _ in range(size // len(chunk)):
            yield chunk
        yield b"\r\n" + _close()

    headers = {**MULTIPART, TOKEN_HEADER: app.state.container.session_token}
    tracemalloc.start()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=LOCAL) as c:
            res = await c.post("/api/documents", content=body(), headers=headers)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert res.status_code == 201, res.text
    assert res.json()["size_bytes"] == size + 5
    assert peak < 2 * size, f"peak {peak / size:.2f}x 파일 크기"
