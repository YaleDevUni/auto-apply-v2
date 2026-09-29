"""프로필·지식베이스 저장소 contract test (§A7, ports/profile_store.py).

`uow_factory`(tests/conftest.py)가 memory·sqlite 둘 다로 돈다 — 매 검증은 쓰기 UoW 와 다른
UoW 로 다시 읽어 실제 라운드트립을 본다.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from auto_apply.contracts.experience import Experience, ExperienceFact, ExperienceSection
from auto_apply.contracts.knowledge import Answer, DocumentMeta
from auto_apply.contracts.profile import (
    AdditionalInfo,
    EducationEntry,
    LanguageEntry,
    MilitaryService,
    Profile,
    ProfileLink,
)
from auto_apply.domain.enums import DocumentKind, ExperienceKind, MilitaryStatus
from auto_apply.domain.errors import AnswerKeyConflict, UniqueIdentifierRejected

AT = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)


async def _write(uow_factory, fn) -> None:
    async with uow_factory() as uow:
        await fn(uow)
        await uow.commit()


# ── Profile ──────────────────────────────────────────────────────────────


def _profile(**kw) -> Profile:
    base = dict(
        user_id="u1",
        name="홍길동",
        phone="+821000000000",
        email="hong@example.com",
        links=[ProfileLink(label="GitHub", url="https://github.com/example")],
        education=[EducationEntry(school="예시대학교", period="2020.03 - 2024.02")],
        skills=["Python"],
        languages=[LanguageEntry(name="영어", level="비즈니스")],
        additional=AdditionalInfo(
            military=MilitaryService(status=MilitaryStatus.COMPLETED, branch="육군"),
            veteran=False,
            desired_salary="회사 내규에 따름",
        ),
    )
    return Profile(**{**base, **kw})


async def test_profile_roundtrip_keeps_additional_info(uow_factory):
    await _write(uow_factory, lambda uow: uow.profiles.save(_profile()))
    async with uow_factory() as uow:
        got = await uow.profiles.get("u1")
    assert got == _profile()
    assert got is not None
    assert got.additional.disability is None  # 비워 둔 추가 정보는 "아직 모름" 그대로 (D10)


async def test_profile_save_overwrites(uow_factory):
    await _write(uow_factory, lambda uow: uow.profiles.save(_profile()))
    await _write(uow_factory, lambda uow: uow.profiles.save(_profile(name="김철수")))
    async with uow_factory() as uow:
        got = await uow.profiles.get("u1")
    assert got is not None and got.name == "김철수"


async def test_profile_get_unknown_is_none(uow_factory):
    async with uow_factory() as uow:
        assert await uow.profiles.get("nobody") is None


# ── Experience ───────────────────────────────────────────────────────────


def _experience(exp_id: str, user_id: str = "u1", **kw) -> Experience:
    base = dict(
        id=exp_id,
        user_id=user_id,
        kind=ExperienceKind.COMPANY,
        name="Acme",
        role="백엔드",
        period="2022.01 - 2023.12",
        skills=["Python"],
        document_ids=["doc-1"],
        facts=[ExperienceFact(id=f"{exp_id}-f0", text="Acme 백엔드 엔지니어로 근무")],
        sections=[
            ExperienceSection(
                key="pay",
                title="결제 API",
                facts=[ExperienceFact(id=f"{exp_id}-f1", text="결제 API 설계", skills=["FastAPI"])],
            )
        ],
    )
    return Experience(**{**base, **kw})


async def test_experience_crud(uow_factory):
    exp = _experience("e1")
    await _write(uow_factory, lambda uow: uow.experiences.save(exp))
    async with uow_factory() as uow:
        assert await uow.experiences.get("e1") == exp

    changed = _experience("e1", name="Acme Corp")
    await _write(uow_factory, lambda uow: uow.experiences.save(changed))
    async with uow_factory() as uow:
        assert await uow.experiences.get("e1") == changed

    async with uow_factory() as uow:
        assert await uow.experiences.delete("e1") is True
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.experiences.get("e1") is None
        assert await uow.experiences.delete("e1") is False


async def test_experience_list_keeps_insertion_order_across_updates(uow_factory):
    """경험 순서 = 이력서 등장 순서. 앞의 항목을 고쳐도 맨 뒤로 가지 않는다."""

    async def seed(uow):
        for i in ("e1", "e2", "e3"):
            await uow.experiences.save(_experience(i))
        await uow.experiences.save(_experience("x1", user_id="other"))

    await _write(uow_factory, seed)
    await _write(uow_factory, lambda uow: uow.experiences.save(_experience("e1", name="수정")))
    async with uow_factory() as uow:
        listed = await uow.experiences.list_for_user("u1")
    assert [e.id for e in listed] == ["e1", "e2", "e3"]
    assert listed[0].name == "수정"


# ── AnswerKB ─────────────────────────────────────────────────────────────


def _answer(answer_id: str, key: str = "desired_salary", **kw) -> Answer:
    base = dict(
        id=answer_id,
        user_id="u1",
        question_key=key,
        answer="회사 내규에 따름",
        source_application_id="app_1",
        updated_at=AT,
    )
    return Answer(**{**base, **kw})


async def test_answer_crud_and_find_by_key(uow_factory):
    await _write(uow_factory, lambda uow: uow.answers.save(_answer("a1")))
    async with uow_factory() as uow:
        assert await uow.answers.get("a1") == _answer("a1")
        assert await uow.answers.find("u1", "desired_salary") == _answer("a1")
        assert await uow.answers.find("u2", "desired_salary") is None

    updated = _answer("a1", answer="5,000만원", updated_at=AT + timedelta(days=1))
    await _write(uow_factory, lambda uow: uow.answers.save(updated))
    async with uow_factory() as uow:
        assert await uow.answers.get("a1") == updated
        assert await uow.answers.delete("a1") is True
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.answers.get("a1") is None
        assert await uow.answers.delete("a1") is False


async def test_answer_updated_at_roundtrips_as_same_instant(uow_factory):
    """다른 시간대로 저장해도 같은 순간으로 돌아온다 (SQLite 는 tzinfo 를 버린다)."""
    kst = timezone(timedelta(hours=9))
    await _write(
        uow_factory, lambda uow: uow.answers.save(_answer("a1", updated_at=AT.astimezone(kst)))
    )
    async with uow_factory() as uow:
        got = await uow.answers.get("a1")
    assert got is not None and got.updated_at == AT


async def test_answer_list_is_per_user_sorted_by_key(uow_factory):
    async def seed(uow):
        await uow.answers.save(_answer("a1", key="residence"))
        await uow.answers.save(_answer("a2", key="available_from"))
        await uow.answers.save(_answer("a3", user_id="other"))

    await _write(uow_factory, seed)
    async with uow_factory() as uow:
        listed = await uow.answers.list_for_user("u1")
    assert [a.question_key for a in listed] == ["available_from", "residence"]


async def test_answer_same_key_with_other_id_conflicts(uow_factory):
    await _write(uow_factory, lambda uow: uow.answers.save(_answer("a1")))
    async with uow_factory() as uow:
        with pytest.raises(AnswerKeyConflict):
            await uow.answers.save(_answer("a2"))
    async with uow_factory() as uow:
        # 다른 사용자는 같은 키를 쓸 수 있다
        await uow.answers.save(_answer("a3", user_id="other"))
        await uow.commit()
    async with uow_factory() as uow:
        assert (await uow.answers.find("u1", "desired_salary")) == _answer("a1")


# ── Document ─────────────────────────────────────────────────────────────


def _document(doc_id: str, **kw) -> DocumentMeta:
    base = dict(
        id=doc_id,
        user_id="u1",
        filename="포트폴리오.pdf",
        content_type="application/pdf",
        size_bytes=1234,
        blob_key=f"documents/{doc_id}.pdf",
        created_at=AT,
    )
    return DocumentMeta(**{**base, **kw})


async def test_document_crud(uow_factory):
    doc = _document("d1")
    await _write(uow_factory, lambda uow: uow.documents.save(doc))
    async with uow_factory() as uow:
        assert await uow.documents.get("d1") == doc

    generated = _document("d1", kind=DocumentKind.GENERATED, fact_ids=["f1", "f2"])
    await _write(uow_factory, lambda uow: uow.documents.save(generated))
    async with uow_factory() as uow:
        assert await uow.documents.get("d1") == generated
        assert await uow.documents.delete("d1") is True
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.documents.get("d1") is None
        assert await uow.documents.delete("d1") is False


async def test_document_list_is_per_user_oldest_first(uow_factory):
    async def seed(uow):
        await uow.documents.save(_document("d2", created_at=AT + timedelta(hours=1)))
        await uow.documents.save(_document("d1"))
        await uow.documents.save(_document("dx", user_id="other"))

    await _write(uow_factory, seed)
    async with uow_factory() as uow:
        assert [d.id for d in await uow.documents.list_for_user("u1")] == ["d1", "d2"]


# ── 트랜잭션 ─────────────────────────────────────────────────────────────


async def test_uncommitted_profile_writes_are_rolled_back(uow_factory, request):
    if request.node.callspec.params["uow_factory"] == "memory":
        pytest.skip("memory 대역은 트랜잭션이 없다")
    async with uow_factory() as uow:
        await uow.profiles.save(_profile())
        await uow.experiences.save(_experience("e1"))
        await uow.answers.save(_answer("a1"))
        await uow.documents.save(_document("d1"))
    async with uow_factory() as uow:
        assert await uow.profiles.get("u1") is None
        assert await uow.experiences.list_for_user("u1") == []
        assert await uow.answers.list_for_user("u1") == []
        assert await uow.documents.list_for_user("u1") == []


# ── 고유식별정보: 저장 시점 재검사 (절대 규칙 5, 방어 심층) ─────────────────────────
# `model_copy(update=...)`·`model_construct` 는 DTO 검증기를 건너뛴다 — 저장소가 막아야 한다.

RRN = "900101-1234567"


def _bypassing_values():
    yield "profile_copy", "profiles", "u1", _profile().model_copy(update={"name": RRN})
    yield (
        "profile_construct",
        "profiles",
        "u1",
        Profile.model_construct(**{**_profile().__dict__, "phone": RRN}),
    )
    yield "experience", "experiences", "e1", _experience("e1").model_copy(update={"role": RRN})
    yield "answer", "answers", "a1", _answer("a1").model_copy(update={"answer": RRN})
    yield (
        "document",
        "documents",
        "d1",
        _document("d1").model_copy(update={"filename": f"{RRN}.pdf"}),
    )


@pytest.mark.parametrize(
    ("repo", "key", "value"),
    [pytest.param(r, k, v, id=name) for name, r, k, v in _bypassing_values()],
)
async def test_save_rejects_identifiers_that_bypassed_dto_validation(uow_factory, repo, key, value):
    async with uow_factory() as uow:
        with pytest.raises(UniqueIdentifierRejected) as exc:
            await getattr(uow, repo).save(value)
        await uow.commit()
    assert "900101" not in str(exc.value)
    async with uow_factory() as uow:
        assert await getattr(uow, repo).get(key) is None
