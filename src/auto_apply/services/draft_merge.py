"""초안 → 본 프로필·경험 병합 규칙 (§A7). 순수 함수.

사용자가 고른 필드만 합친다. 이미 있는 값을 지우지 않는다 — 초안이 비어 있는 칸은 기존 값을
두고, 목록은 없는 항목만 뒤에 붙인다. 초안 편집(PUT)으로 값을 고친 뒤 확정하면 고친 값이 들어간다.
"""

from typing import Any

from auto_apply.ai.profile_extraction import ExtractedExperience, ExtractedProfile
from auto_apply.contracts.profile import AdditionalInfo, Profile
from auto_apply.domain.errors import InvalidInput
from auto_apply.services.profile_draft_types import ProfileField


def merge_profile(
    current: Profile | None, draft: ExtractedProfile, fields: set[ProfileField], user_id: str
) -> Profile:
    base = current or Profile(user_id=user_id, name="")
    update: dict[str, object] = {}
    for f in (ProfileField.NAME, ProfileField.PHONE, ProfileField.EMAIL):
        value = getattr(draft, f.value)
        if f in fields and value:
            update[f.value] = value
    if ProfileField.LINKS in fields:
        urls = {link.url for link in base.links}
        update["links"] = [*base.links, *(x for x in draft.links if x.url not in urls)]
    if ProfileField.EDUCATION in fields:
        seen = {(e.school, e.period) for e in base.education}
        update["education"] = [
            *base.education,
            *(e for e in draft.education if (e.school, e.period) not in seen),
        ]
    if ProfileField.SKILLS in fields:
        known = {s.casefold() for s in base.skills}
        update["skills"] = [*base.skills, *(s for s in draft.skills if s.casefold() not in known)]
    if ProfileField.LANGUAGES in fields:
        names = {x.name for x in base.languages}
        update["languages"] = [
            *base.languages,
            *(x for x in draft.languages if x.name not in names),
        ]
    if ProfileField.ADDITIONAL in fields:
        update["additional"] = _merge_additional(base.additional, draft.additional)
    # model_copy 는 검증을 건너뛴다 — 다시 검증해 고유식별정보 규칙(절대 규칙 5)을 태운다.
    merged = Profile.model_validate(base.model_copy(update=update).model_dump())
    if not merged.name.strip():
        raise InvalidInput("이름이 없다 — 초안의 name 을 고르거나 인적사항을 먼저 저장하라")
    return merged


def _merge_additional(base: AdditionalInfo, draft: AdditionalInfo) -> AdditionalInfo:
    # None 은 "모름"이다(D10) — 초안이 모르는 칸은 기존 값을 둔다.
    known = {k: v for k, v in draft.model_dump().items() if v is not None}
    return AdditionalInfo.model_validate({**base.model_dump(), **known})


def experience_data(item: ExtractedExperience) -> dict[str, Any]:
    """초안 경험 → `build_experience` 입력. id·fact id 는 거기서 발급된다."""
    data = item.model_dump()
    # 섹션 key 는 경험 안에서만 유일하면 된다 — 이력서 블록 id `{experience.id}:{key}` 가 된다.
    data["sections"] = [{**s, "key": f"s{n}"} for n, s in enumerate(data["sections"], start=1)]
    return data
