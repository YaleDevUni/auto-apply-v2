"""LLM 이 쓴 불릿(ai/schemas.ResumeContentSchema)을 결정론적 블록 메타데이터(domain/resume_matching
.FactBlock)와 Profile 에 이어붙여 최종 이력서 콘텐츠(contracts/resume_content.AssembledResume)를
조립한다. `adapters/resume/simple.py`가 이 로직까지 떠안으면 한 파일 책임이 흐려져서 분리했다.
"""

from auto_apply.ai.schemas import ResumeContentSchema, ResumeHighlight
from auto_apply.contracts.profile import Profile
from auto_apply.contracts.resume_content import (
    AssembledResume,
    CareerEntryView,
    ResumeBlockView,
    ResumeBulletView,
)
from auto_apply.domain.resume_blocks import FactBlock


def _to_bullets(items: list[ResumeHighlight]) -> list[ResumeBulletView]:
    return [ResumeBulletView(text=h.text, fact_ids=h.fact_ids) for h in items]


def assemble_resume(
    profile: Profile,
    blocks: list[FactBlock],
    content: ResumeContentSchema,
) -> AssembledResume:
    bullets_by_block: dict[str, list[ResumeHighlight]] = {
        b.block_id: b.bullets for b in content.blocks
    }

    career_order: list[str] = []
    career_blocks: dict[str, list[ResumeBlockView]] = {}
    career_meta: dict[str, tuple[str, str | None]] = {}
    projects: list[ResumeBlockView] = []

    for block in blocks:
        if block.kind == "career" and block.entity not in career_blocks:
            # 회사(entity) 헤더는 그 회사의 첫 블록이 불릿을 하나도 못 냈어도 항상 등록한다 —
            # "어느 회사에서 일했는지"는 사실이라 그 자체는 항상 보여준다(§ resume-block-count-cap
            # 되돌림에서도 유지한 부분).
            career_order.append(block.entity)
            career_blocks[block.entity] = []
            career_meta[block.entity] = (block.entity_label, block.entity_period)

        bullets = _to_bullets(bullets_by_block.get(block.id, []))
        if not bullets:
            # LLM 이 이 블록을 "건너뛰기"로 판단했다는 뜻(ai/prompts.py 가 그렇게 허용한다) —
            # 빈 제목·기간·사용기술 태그만 남은 껍데기 블록을 렌더링하면 안 된다. 코드 상한이
            # 3/4개였을 때는(2026-08-21 이전) 후보 자체가 적어 이 문제가 잘 안 드러났는데, 상한을
            # 안전판(20)으로 올려 LLM 판단에 맡기게 되면서 스킵이 잦아져 실제로 라이브에서
            # 관측됐다 — 실측(DevOps/클라우드마이그레이션/yt-sub-mcp 등 불릿 0개 블록이 빈
            # 헤더로 남던 문제).
            continue

        view = ResumeBlockView(
            title=block.title,
            period=block.period,
            url=block.entity_url,
            bullets=bullets,
            tech_stack=block.tech_stack,
        )
        if block.kind == "career":
            career_blocks[block.entity].append(view)
        else:
            projects.append(view)

    career = [
        CareerEntryView(
            company=career_meta[entity][0],
            period=career_meta[entity][1],
            blocks=career_blocks[entity],
        )
        for entity in career_order
    ]

    return AssembledResume(
        name=profile.name,
        phone=profile.phone,
        email=profile.email,
        summary=content.summary,
        highlights=_to_bullets(content.highlights),
        career=career,
        projects=projects,
        ai_usage=_to_bullets(content.ai_usage),
        education=profile.education,
        skills=profile.skills,
        languages=profile.languages,
        caution_notes=content.caution_notes,
    )


def used_fact_ids(content: ResumeContentSchema) -> list[str]:
    ids: set[str] = {fid for h in content.highlights for fid in h.fact_ids}
    ids |= {fid for h in content.ai_usage for fid in h.fact_ids}
    ids |= {fid for b in content.blocks for h in b.bullets for fid in h.fact_ids}
    return sorted(ids)
