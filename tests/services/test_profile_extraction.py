"""이력서 파일 → 초안 추출 회귀 (T1.3): 픽스처 2종 x stub LLM 고정 응답, 잘못된 응답 거부.

추출기는 실제 구현(pypdf)으로 픽스처 PDF 를 읽는다 — 프롬프트에 원문이 실제로 실리는지까지 본다.
"""

import json
from pathlib import Path

import pytest

from auto_apply.adapters.extract.fake import FakeTextExtractor
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.ai.profile_extraction import ProfileExtraction
from auto_apply.domain.enums import ExperienceKind, MilitaryStatus
from auto_apply.domain.errors import (
    LLMSchemaViolation,
    NotFound,
    TextExtractionFailed,
    UniqueIdentifierRejected,
    UploadRejected,
)
from auto_apply.domain.unique_identifiers import (
    REDACTION_MARK,
    contains_resident_registration_number,
)
from auto_apply.services.profile_draft_types import DraftSource
from tests.services.fakes import RecordingLLM, make_draft_service

RESUMES = Path(__file__).parents[1] / "fixtures" / "resumes"
USER = "u1"


def _fixture(name: str) -> tuple[bytes, dict]:
    pdf = (RESUMES / f"{name}_resume.pdf").read_bytes()
    payload = json.loads((RESUMES / f"{name}_extraction.json").read_text(encoding="utf-8"))
    return pdf, payload


async def _upload(svc, data: bytes, filename: str = "resume.pdf") -> str:
    meta = await svc._uploads.upload_document(USER, filename, data)
    return meta.id


@pytest.mark.parametrize("name", ["dev", "nondev"])
async def test_fixture_resume_to_draft(uow_factory, name):
    pdf, payload = _fixture(name)
    llm = RecordingLLM(payloads=[payload])
    svc = make_draft_service(uow_factory, llm=llm)
    doc_id = await _upload(svc, pdf)

    draft = await svc.extract_from_document(USER, doc_id)

    assert draft.source is DraftSource.RESUME and draft.document_id == doc_id
    assert draft.content == ProfileExtraction.model_validate(payload)
    assert await svc.get_draft(USER, draft.id) == draft  # 저장됐다
    # 원문이 프롬프트에 실렸고, 고정 응답의 fact 는 전부 원문에 있는 문장이다 (지어낸 값 없음).
    [prompt] = llm.prompts
    for exp in draft.content.experiences:
        for fact in exp.facts:
            assert fact.text in prompt
    assert draft.content.profile.name in prompt


async def test_fixture_specifics():
    """직군별 차이가 스키마에 담기는지 — 개발(프로젝트·링크) / 비개발(활동·병역)."""
    dev = ProfileExtraction.model_validate(_fixture("dev")[1])
    nondev = ProfileExtraction.model_validate(_fixture("nondev")[1])
    assert [e.kind for e in dev.experiences] == [ExperienceKind.COMPANY, ExperienceKind.PROJECT]
    assert dev.experiences[1].url == "https://github.com/example-gasang/schedule-bot"
    assert [e.kind for e in nondev.experiences] == [ExperienceKind.COMPANY, ExperienceKind.ACTIVITY]
    assert nondev.profile.additional.military is not None
    assert nondev.profile.additional.military.status is MilitaryStatus.NOT_APPLICABLE
    assert nondev.profile.additional.veteran is None  # 원문에 없으면 "모름" (D10)


_GOOD = _fixture("dev")[1]
_BAD = {
    "unknown-kind": {"experiences": [{**_GOOD["experiences"][0], "kind": "job"}]},
    "extra-field": {**_GOOD, "confidence": 0.9},
    "empty-name": {"experiences": [{**_GOOD["experiences"][0], "name": "  "}]},
    "empty-fact": {"experiences": [{"kind": "project", "name": "x", "facts": [{"text": ""}]}]},
    "id-invented": {
        "experiences": [{"kind": "project", "name": "x", "facts": [{"id": "f1", "text": "a"}]}]
    },
    "rrn": {"profile": {"name": "김가상", "phone": "900101-1234567"}},
    "too-many": {"experiences": [{"kind": "project", "name": f"p{i}"} for i in range(201)]},
    "not-object": {"profile": "김가상"},
}


@pytest.mark.parametrize("payload", list(_BAD.values()), ids=list(_BAD))
async def test_invalid_llm_output_rejected_and_nothing_stored(uow_factory, payload):
    pdf, _ = _fixture("dev")
    llm = RecordingLLM(payloads=[payload] * 3)
    store = InMemoryBlobStore()
    svc = make_draft_service(uow_factory, llm=llm, store=store)
    doc_id = await _upload(svc, pdf)

    with pytest.raises(LLMSchemaViolation) as exc:
        await svc.extract_from_document(USER, doc_id)

    assert len(llm.prompts) == 3  # 2회 재프롬프트 뒤 포기
    assert "900101-1234567" not in str(exc.value)  # 거부한 번호를 에러로 흘리지 않는다
    assert await store.list_keys("drafts/") == []


async def test_reprompt_recovers_after_bad_output(uow_factory):
    pdf, payload = _fixture("nondev")
    llm = RecordingLLM(payloads=[_BAD["unknown-kind"], payload])
    svc = make_draft_service(uow_factory, llm=llm)
    draft = await svc.extract_from_document(USER, await _upload(svc, pdf))
    assert draft.content.profile.name == "이예시"
    assert "[이전 시도 오류]" in llm.prompts[1]


async def test_rrn_is_redacted_before_llm_and_only_counted(uow_factory):
    """주민번호 꼴은 LLM 대역에 가린 텍스트로만 닿고, 초안에는 개수만 남는다 (절대 규칙 5)."""
    data = b"%PDF-fake"
    zw = "\u200b"
    text = f"김가상\n주민등록번호 900101-1234567\n배우자 8501{zw}01 2123456\n경력 3년"
    llm = RecordingLLM(payloads=[_GOOD])
    store = InMemoryBlobStore()
    svc = make_draft_service(
        uow_factory, llm=llm, extractor=FakeTextExtractor({data: text}), store=store
    )

    draft = await svc.extract_from_file(USER, "이력서.pdf", data)

    [prompt] = llm.prompts
    assert not contains_resident_registration_number(prompt)
    assert prompt.count(REDACTION_MARK) == 2
    assert "김가상" in prompt and "경력 3년" in prompt
    assert draft.redacted_identifiers == 2
    assert draft.source_filename == "이력서.pdf" and draft.document_id is None
    # 원문도, 가린 위치도 어디에도 저장되지 않는다 — 초안 JSON 과 문서 저장소 모두.
    assert await store.list_keys("documents/") == []
    [key] = await store.list_keys("drafts/")
    stored = (await store.get(key)).decode()
    assert "1234567" not in stored and "2123456" not in stored and REDACTION_MARK not in stored


async def test_extract_from_file_checks_type_and_filename(uow_factory):
    llm = RecordingLLM(payloads=[_GOOD])
    svc = make_draft_service(uow_factory, llm=llm)
    with pytest.raises(UploadRejected):
        await svc.extract_from_file(USER, "resume.txt", b"plain text")
    with pytest.raises(TextExtractionFailed):  # 받는 형식이지만 글자가 없다
        await svc.extract_from_file(USER, "photo.png", b"\x89PNG\r\n\x1a\n...")
    with pytest.raises(UniqueIdentifierRejected):
        await svc.extract_from_file(USER, "900101-1234567.pdf", _fixture("dev")[0])
    assert llm.prompts == []  # 파일명에 번호가 있으면 LLM 을 부르기 전에 멈춘다


async def test_unreadable_document_and_other_owner(uow_factory):
    svc = make_draft_service(uow_factory, extractor=FakeTextExtractor({}))
    png = await _upload(svc, b"\x89PNG\r\n\x1a\n....", "photo.png")
    with pytest.raises(TextExtractionFailed):
        await svc.extract_from_document(USER, png)
    with pytest.raises(NotFound):
        await svc.extract_from_document("someone-else", png)


async def test_text_length_cap(uow_factory):
    data = b"%PDF-long"
    svc = make_draft_service(uow_factory, extractor=FakeTextExtractor({data: "가" * 50_001}))
    with pytest.raises(ValueError, match="상한"):
        await svc.extract_from_document(USER, await _upload(svc, data))
