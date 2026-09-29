"""ProfileService — 저장소가 강제하지 않는 규칙(id 발급·유일성·참조·정규화) (T1.2)."""

import pytest
from pydantic import ValidationError

from auto_apply.adapters.extract.pdf_docx import PdfDocxTextExtractor
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.contracts.profile import Profile
from auto_apply.domain.enums import ExperienceKind
from auto_apply.domain.errors import (
    AnswerKeyConflict,
    InvalidInput,
    NotFound,
    UniqueIdentifierRejected,
)
from auto_apply.services.profile import ProfileService
from auto_apply.services.uploads import UploadService
from tests.services.fakes import FixedClock, SeqIds

PDF = b"%PDF-1.7\n..."
RRN = "900101-1234567"


@pytest.fixture
def svc(uow_factory) -> ProfileService:
    return ProfileService(uow_factory, FixedClock(), SeqIds())


@pytest.fixture
def uploads(uow_factory) -> UploadService:
    return UploadService(
        uow_factory, InMemoryBlobStore(), PdfDocxTextExtractor(), FixedClock(), SeqIds()
    )


def _exp(**kw):
    base = {
        "kind": ExperienceKind.COMPANY,
        "name": "가상회사",
        "facts": [{"text": "지표 30% 개선"}],
    }
    return {**base, **kw}


# ── 인적사항 ─────────────────────────────────────────────────────────────────


async def test_profile_roundtrip_and_missing(svc):
    with pytest.raises(NotFound):
        await svc.get_profile("u1")
    await svc.save_profile(Profile(user_id="u1", name="홍길동"))
    assert (await svc.get_profile("u1")).name == "홍길동"


async def test_profile_blank_name_rejected(svc):
    with pytest.raises(InvalidInput):
        await svc.save_profile(Profile(user_id="u1", name="  "))


async def test_profile_rrn_rejected_even_if_dto_check_skipped(svc):
    """DTO 검증을 건너뛴 값도 저장 경로에서 막힌다 (절대 규칙 5)."""
    bad = Profile(user_id="u1", name="홍길동").model_copy(update={"phone": RRN})
    with pytest.raises(UniqueIdentifierRejected):
        await svc.save_profile(bad)
    with pytest.raises(NotFound):
        await svc.get_profile("u1")


# ── 경험 ─────────────────────────────────────────────────────────────────────


async def test_create_experience_issues_ids(svc):
    exp = await svc.create_experience(
        "u1",
        _exp(sections=[{"key": "a", "title": "A", "facts": [{"text": "x"}, {"text": "y"}]}]),
    )
    assert exp.id and exp.user_id == "u1"
    ids = [f.id for f in exp.all_facts()]
    assert all(ids) and len(set(ids)) == 3
    assert await svc.get_experience("u1", exp.id) == exp


async def test_fact_ids_unique_per_user(svc):
    first = await svc.create_experience("u1", _exp(facts=[{"id": "f1", "text": "a"}]))
    with pytest.raises(InvalidInput):
        await svc.create_experience("u1", _exp(facts=[{"id": "f1", "text": "b"}]))
    # 다른 사용자는 별개다
    await svc.create_experience("u2", _exp(facts=[{"id": "f1", "text": "b"}]))
    # 자기 자신을 고칠 때 기존 id 를 유지하는 것은 충돌이 아니다
    updated = await svc.update_experience(
        "u1", first.id, _exp(facts=[{"id": "f1", "text": "a2"}, {"text": "new"}])
    )
    assert [f.text for f in updated.facts] == ["a2", "new"]
    assert updated.facts[0].id == "f1"
    assert len(await svc.list_experiences("u1")) == 1


async def test_duplicate_fact_ids_within_experience_rejected(svc):
    with pytest.raises(ValidationError):
        await svc.create_experience(
            "u1", _exp(facts=[{"id": "f1", "text": "a"}, {"id": "f1", "text": "b"}])
        )


async def test_experience_document_ids_must_exist(svc, uploads):
    with pytest.raises(InvalidInput):
        await svc.create_experience("u1", _exp(document_ids=["nope"]))
    doc = await uploads.upload_document("u1", "a.pdf", PDF)
    exp = await svc.create_experience("u1", _exp(document_ids=[doc.id]))
    assert exp.document_ids == [doc.id]


async def test_experience_owner_checked(svc):
    exp = await svc.create_experience("u1", _exp())
    for call in (
        svc.get_experience("u2", exp.id),
        svc.update_experience("u2", exp.id, _exp()),
        svc.delete_experience("u2", exp.id),
        svc.get_experience("u1", "missing"),
    ):
        with pytest.raises(NotFound):
            await call
    await svc.delete_experience("u1", exp.id)
    assert await svc.list_experiences("u1") == []


async def test_experience_rrn_rejected(svc):
    with pytest.raises(ValidationError):
        await svc.create_experience("u1", _exp(role=f"주민번호 {RRN}"))
    assert await svc.list_experiences("u1") == []


# ── 답변KB ───────────────────────────────────────────────────────────────────


async def test_answer_create_find_update_delete(svc):
    a = await svc.create_answer("u1", "희망 연봉은?*", "회사 내규", "app_1")
    assert a.question_key == "희망 연봉은"
    assert (await svc.find_answer("u1", "  희망 연봉은 ")) == a
    assert await svc.find_answer("u2", "희망 연봉은") is None

    b = await svc.update_answer("u1", a.id, "희망 연봉은", "4,000만원")
    assert b.answer == "4,000만원"
    assert b.source_application_id == "app_1"  # 비워 보내면 처음 출처 유지

    await svc.delete_answer("u1", a.id)
    assert await svc.list_answers("u1") == []
    with pytest.raises(NotFound):
        await svc.get_answer("u1", a.id)


async def test_answer_same_normalized_key_conflicts(svc):
    await svc.create_answer("u1", "입사 가능일?", "즉시")
    with pytest.raises(AnswerKeyConflict):
        await svc.create_answer("u1", "입사  가능일 :", "다음 달")
    other = await svc.create_answer("u1", "거주 지역", "서울")
    with pytest.raises(AnswerKeyConflict):
        await svc.update_answer("u1", other.id, "입사 가능일", "서울")


async def test_answer_rrn_rejected(svc):
    with pytest.raises(ValidationError):
        await svc.create_answer("u1", "주민등록번호", RRN)
    assert await svc.list_answers("u1") == []
