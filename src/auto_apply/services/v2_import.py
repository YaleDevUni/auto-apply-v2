"""v2 `config/profile.yaml`·`config/facts.yaml` → 프로필 초안 (§A7). 순수 변환 — LLM 없음.

v2 fact 는 평평한 목록에 `entity`/`block` 그룹핑 키를 반복해 적었다. 여기서 그 키로 묶어
Experience 초안으로 되돌린다 — `domain/experience_facts.py` 의 역방향이다:
- 그룹 = `entity`(없으면 v2 `kind`). 그룹의 종류는 첫 fact 의 kind: experience→company,
  project→project, education→education, 그 밖(skill 등)→activity.
- `block` 없는 fact 는 경험 자신의 facts(헤더 근거), `block` 별로는 section 하나.
  블록이 `main` 하나뿐이고 헤더 fact 가 없으면(v2 개인 프로젝트 관례) section 없이 facts 로 편다.
- 이름·역할은 `entity_label` 의 "이름(역할)" 꼴을 되돌린다(회사만). v2 fact id 는 버린다 — 확정할 때
  새로 발급한다.
v2 파일은 1인 1사용자였다. `user_id` 가 둘 이상 섞여 있으면 어느 쪽을 가져올지 추측하지 않고
거부한다.
"""

import re
from typing import Any

import yaml
from pydantic import Field

from auto_apply.ai.profile_extraction import (
    ExtractedExperience,
    ExtractedFact,
    ExtractedProfile,
    ExtractedSection,
    ProfileExtraction,
)
from auto_apply.contracts._base import Frozen
from auto_apply.contracts.fact import Fact
from auto_apply.contracts.profile import EducationEntry, LanguageEntry
from auto_apply.domain.enums import ExperienceKind
from auto_apply.domain.errors import InvalidInput

_KIND = {
    "experience": ExperienceKind.COMPANY,
    "project": ExperienceKind.PROJECT,
    "education": ExperienceKind.EDUCATION,
}
_MAIN_BLOCK = "main"
_LABEL = re.compile(r"^(?P<name>.+?)\s*\((?P<role>[^()]+)\)$")


class V2Profile(Frozen):
    """v2 `contracts/profile.py` 그대로 (legacy/v2-telegram-recipe)."""

    user_id: str
    name: str
    phone: str = ""
    email: str = ""
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    languages: list[LanguageEntry] = Field(default_factory=list)


def import_v2_yaml(profile_yaml: str | None, facts_yaml: str | None) -> ProfileExtraction:
    profiles = [V2Profile.model_validate(x) for x in _load_list(profile_yaml, "profile.yaml")]
    facts = [Fact.model_validate(x) for x in _load_list(facts_yaml, "facts.yaml")]
    if not profiles and not facts:
        raise InvalidInput("가져올 내용이 없다 — profile.yaml·facts.yaml 중 하나는 있어야 한다")
    if len({p.user_id for p in profiles} | {f.user_id for f in facts}) > 1:
        raise InvalidInput("user_id 가 여러 개다 — 가져올 사용자 하나만 남기고 다시 시도하라")
    return ProfileExtraction(
        profile=_profile(profiles[0]) if profiles else ExtractedProfile(),
        experiences=[_experience(group) for group in _groups(facts)],
    )


def _load_list(text: str | None, where: str) -> list[Any]:
    if text is None or not text.strip():
        return []
    try:
        # 앵커·별칭은 v2 파일에 쓰인 적 없다 — 받으면 "billion laughs" 식 확장 공격 통로만 된다.
        if any(isinstance(e, yaml.AliasEvent) for e in yaml.parse(text, Loader=yaml.SafeLoader)):
            raise InvalidInput(f"{where}: YAML 별칭(*)은 지원하지 않는다")
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        # 에러 문자열은 원문 줄을 인용한다 — 위치만 알린다.
        mark = getattr(e, "problem_mark", None)
        line = f" {mark.line + 1}행" if mark is not None else ""
        raise InvalidInput(f"{where}: YAML 형식이 올바르지 않다{line}") from None
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise InvalidInput(f"{where}: 최상위는 항목 목록이어야 한다")
    return data


def _profile(p: V2Profile) -> ExtractedProfile:
    return ExtractedProfile(
        name=p.name,
        phone=p.phone,
        email=p.email,
        education=p.education,
        skills=p.skills,
        languages=p.languages,
    )


def _groups(facts: list[Fact]) -> list[list[Fact]]:
    groups: dict[str, list[Fact]] = {}
    for f in facts:
        groups.setdefault(f"entity:{f.entity}" if f.entity else f"kind:{f.kind}", []).append(f)
    return list(groups.values())


def _first(values: list[str | None]) -> str | None:
    return next((v for v in values if v), None)


def _experience(group: list[Fact]) -> ExtractedExperience:
    head = group[0]
    kind = _KIND.get(head.kind, ExperienceKind.ACTIVITY)
    header = [f for f in group if not f.block]
    blocks: dict[str, list[Fact]] = {}
    for f in group:
        if f.block:
            blocks.setdefault(f.block, []).append(f)

    label = _first([f.entity_label for f in group])
    name, role = label or "", ""
    if label and kind is ExperienceKind.COMPANY and (m := _LABEL.match(label)):
        name, role = m["name"], m["role"]
    flatten = not header and list(blocks) == [_MAIN_BLOCK]
    main = blocks.get(_MAIN_BLOCK, [])
    if not name:
        name = (_first([f.block_label for f in main]) if flatten else None) or (
            head.entity or head.kind
        )
    period = _first([f.entity_period for f in group])
    if flatten and not period:
        period = _first([f.block_period for f in main])

    sections = [
        ExtractedSection(
            title=_first([f.block_label for f in fs]) or key,
            period=_first([f.block_period for f in fs]) or "",
            facts=[_fact(f) for f in fs],
        )
        for key, fs in blocks.items()
    ]
    return ExtractedExperience(
        kind=kind,
        name=name,
        role=role,
        period=period or "",
        url=_first([f.entity_url for f in group]),
        skills=list(dict.fromkeys(k for f in group for k in f.keywords)),
        facts=[_fact(f) for f in (main if flatten else header)],
        sections=[] if flatten else sections,
    )


def _fact(f: Fact) -> ExtractedFact:
    return ExtractedFact(text=f.content, skills=f.keywords)
