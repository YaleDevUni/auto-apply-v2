"""초안 → 확정 병합 (T1.3): 항목별 선택, 기존 프로필 보존, 한 트랜잭션, 확정 뒤 초안 삭제."""

from datetime import timedelta

import pytest

from auto_apply.adapters.storage.local import LocalBlobStore
from auto_apply.adapters.storage.memory import InMemoryBlobStore
from auto_apply.ai.profile_extraction import ProfileExtraction
from auto_apply.contracts.profile import AdditionalInfo, Profile, ProfileLink
from auto_apply.domain.enums import ExperienceKind
from auto_apply.domain.errors import InvalidInput, NotFound, UniqueIdentifierRejected
from auto_apply.services.draft_merge import merge_profile
from auto_apply.services.profile_draft_types import DraftSelection, DraftSource, ProfileField
from tests.services.fakes import make_draft_service

USER = "u1"
CONTENT = {
    "profile": {
        "name": "김가상",
        "email": "new@example.com",
        "links": [
            {"label": "GitHub", "url": "https://github.com/example"},
            {"label": "블로그", "url": "https://blog.example.com"},
        ],
        "skills": ["Python", "fastapi", "Redis"],
        "additional": {"veteran": False},
    },
    "experiences": [
        {
            "kind": "company",
            "name": "가상결제",
            "role": "백엔드",
            "facts": [{"text": "헤더 서술"}],
            "sections": [
                {"title": "결제 API", "facts": [{"text": "일 12만 건 처리"}]},
                {"title": "정산 배치", "facts": [{"text": "40분→9분"}]},
            ],
        },
        {"kind": "project", "name": "일정 봇", "facts": [{"text": "스타 150개"}]},
        {"kind": "activity", "name": "봉사단", "facts": [{"text": "홍보물 12건"}]},
    ],
}


async def _draft(svc):
    return await svc._create(USER, DraftSource.V2_YAML, ProfileExtraction.model_validate(CONTENT))


async def test_confirm_selected_items_only(uow_factory):
    svc = make_draft_service(uow_factory)
    draft = await _draft(svc)

    result = await svc.confirm(
        USER,
        draft.id,
        DraftSelection(
            profile_fields=[ProfileField.NAME, ProfileField.SKILLS], experience_indexes=[0, 2]
        ),
    )

    assert result.profile is not None
    assert result.profile.name == "김가상"
    assert result.profile.email == ""  # 고르지 않은 필드는 들어오지 않는다
    assert [e.name for e in result.experiences] == ["가상결제", "봉사단"]
    company = result.experiences[0]
    assert [s.key for s in company.sections] == ["s1", "s2"]
    fact_ids = [f.id for f in company.all_facts()]
    assert all(fact_ids) and len(set(fact_ids)) == 3  # 서버가 발급
    async with uow_factory() as uow:
        assert await uow.profiles.get(USER) == result.profile
        stored = await uow.experiences.list_for_user(USER)
    assert [e.kind for e in stored] == [ExperienceKind.COMPANY, ExperienceKind.ACTIVITY]
    with pytest.raises(NotFound):  # 확정하면 초안은 사라진다
        await svc.get_draft(USER, draft.id)


async def test_confirm_merges_into_existing_profile(uow_factory):
    svc = make_draft_service(uow_factory)
    async with uow_factory() as uow:
        await uow.profiles.save(
            Profile(
                user_id=USER,
                name="기존이름",
                phone="010-1111-2222",
                links=[ProfileLink(label="GH", url="https://github.com/example")],
                skills=["FastAPI"],
                additional=AdditionalInfo(disability=False, veteran=True),
            )
        )
        await uow.commit()
    draft = await _draft(svc)
    every = list(ProfileField)

    result = await svc.confirm(USER, draft.id, DraftSelection(profile_fields=every))

    p = result.profile
    assert p is not None
    assert (p.name, p.phone, p.email) == ("김가상", "010-1111-2222", "new@example.com")
    assert [link.url for link in p.links] == [
        "https://github.com/example",
        "https://blog.example.com",
    ]
    assert p.skills == ["FastAPI", "Python", "Redis"]  # 대소문자만 다른 중복은 붙이지 않는다
    # None(모름)인 초안 칸은 기존 값을 두고, 값이 있는 칸만 덮는다.
    assert p.additional.disability is False and p.additional.veteran is False
    assert result.experiences == []


async def test_confirm_without_profile_fields_leaves_profile_alone(uow_factory):
    svc = make_draft_service(uow_factory)
    draft = await _draft(svc)
    result = await svc.confirm(USER, draft.id, DraftSelection(experience_indexes=[1, 1]))
    assert result.profile is None
    assert [e.name for e in result.experiences] == ["일정 봇"]  # 중복 선택은 한 번만
    async with uow_factory() as uow:
        assert await uow.profiles.get(USER) is None


@pytest.mark.parametrize(
    "selection",
    [DraftSelection(experience_indexes=[3]), DraftSelection(experience_indexes=[-1])],
)
async def test_confirm_rejects_out_of_range_and_keeps_draft(uow_factory, selection):
    svc = make_draft_service(uow_factory)
    draft = await _draft(svc)
    with pytest.raises(InvalidInput):
        await svc.confirm(USER, draft.id, selection)
    assert await svc.get_draft(USER, draft.id) == draft


async def test_confirm_needs_a_name_for_new_profile(uow_factory):
    svc = make_draft_service(uow_factory)
    draft = await _draft(svc)
    with pytest.raises(InvalidInput):
        await svc.confirm(
            USER,
            draft.id,
            DraftSelection(profile_fields=[ProfileField.EMAIL], experience_indexes=[0]),
        )
    async with uow_factory() as uow:  # 한 트랜잭션 — 경험도 들어가지 않았다
        assert await uow.experiences.list_for_user(USER) == []
    assert await svc.get_draft(USER, draft.id) == draft


async def test_update_draft_then_confirm_uses_edited_values(uow_factory):
    svc = make_draft_service(uow_factory)
    draft = await _draft(svc)
    edited = ProfileExtraction.model_validate(
        {**CONTENT, "profile": {**CONTENT["profile"], "name": "김수정"}}
    )
    updated = await svc.update_draft(USER, draft.id, edited)
    assert updated.id == draft.id and updated.created_at == draft.created_at
    result = await svc.confirm(USER, draft.id, DraftSelection(profile_fields=[ProfileField.NAME]))
    assert result.profile is not None and result.profile.name == "김수정"


async def test_draft_ids_are_not_paths(uow_factory, tmp_path):
    """초안 id 는 blob 키에 들어간다 — 다른 blob(업로드 문서)을 읽거나 지우는 통로가 없어야 한다."""
    # LocalBlobStore 는 키를 경로로 푼다 — 메모리 대역으로는 `..` 가 무해해 보여 검사가 안 된다.
    store = LocalBlobStore(tmp_path)
    svc = make_draft_service(uow_factory, store=store)
    await store.put("documents/u1/doc.json", b"{}")
    for bad in [
        "../../documents/u1/doc",
        "../../documents/u1/doc.json#",
        "..",
        "a/b",
        "",
        "x" * 65,
    ]:
        with pytest.raises(NotFound):
            await svc.get_draft(USER, bad)
        with pytest.raises(NotFound):
            await svc.delete_draft(USER, bad)
    assert await store.exists("documents/u1/doc.json")


async def test_drafts_are_per_user(uow_factory):
    svc = make_draft_service(uow_factory)
    draft = await _draft(svc)
    with pytest.raises(NotFound):
        await svc.get_draft("someone-else", draft.id)
    with pytest.raises(NotFound):
        await svc.confirm("someone-else", draft.id, DraftSelection())
    await svc.delete_draft(USER, draft.id)
    with pytest.raises(NotFound):
        await svc.delete_draft(USER, draft.id)


async def test_draft_store_refuses_identifiers_even_past_validation(uow_factory):
    """`model_copy(update=)` 로 검증을 건너뛴 값도 저장 직전에 막힌다 (절대 규칙 5)."""
    svc = make_draft_service(uow_factory)
    draft = await _draft(svc)
    profile = draft.content.profile.model_copy(update={"name": "900101-1234567"})
    content = draft.content.model_copy(update={"profile": profile})
    sneaky = draft.model_copy(update={"content": content})
    with pytest.raises(UniqueIdentifierRejected):
        await svc._drafts.save(sneaky)


def test_merge_profile_revalidates_identifiers():
    draft = ProfileExtraction.model_validate(CONTENT).profile.model_copy(
        update={"phone": "900101-1234567"}
    )
    with pytest.raises(ValueError):
        merge_profile(None, draft, {ProfileField.NAME, ProfileField.PHONE}, USER)


async def test_list_drafts_newest_first_with_summary(uow_factory):
    store = InMemoryBlobStore()
    svc = make_draft_service(uow_factory, store=store)
    older = await _draft(svc)
    newer = await svc._create(
        USER,
        DraftSource.RESUME,
        ProfileExtraction.model_validate({"experiences": CONTENT["experiences"][:1]}),
        source_filename="이력서.pdf",
        redacted_identifiers=1,
    )
    # 고정 시계라 생성 시각이 같다 — 나중 것으로 옮겨 정렬을 본다.
    await svc._drafts.save(newer.model_copy(update={"created_at": older.created_at + timedelta(1)}))
    other = await svc._create("someone-else", DraftSource.V2_YAML, ProfileExtraction())
    # 남의 초안이 이 사용자 경로에 놓여 있어도 목록에 섞이지 않는다.
    await store.put(f"drafts/{USER}/planted.json", other.model_dump_json().encode())
    await store.put(f"drafts/{USER}/broken.json", b"not json")  # 망가진 파일은 건너뛴다

    summaries = await svc.list_drafts(USER)

    assert [s.id for s in summaries] == [newer.id, older.id]
    first, second = summaries
    assert (first.source, first.source_filename, first.redacted_identifiers) == (
        DraftSource.RESUME,
        "이력서.pdf",
        1,
    )
    assert (first.profile_field_count, first.experience_count) == (0, 1)
    # name·email·links·skills·additional(veteran=False) = 5
    assert (second.profile_field_count, second.experience_count) == (5, 3)
    await svc.confirm(USER, older.id, DraftSelection())
    assert [s.id for s in await svc.list_drafts(USER)] == [newer.id]
