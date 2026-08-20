"""LLM 이 쓴 불릿(ai/schemas.ResumeContentSchema)을 결정론적 블록 메타데이터(domain/resume_matching
.FactBlock)와 Profile 에 이어붙여 최종 이력서 콘텐츠(contracts/resume_content.AssembledResume)를
조립한다. `adapters/resume/simple.py`가 이 로직까지 떠안으면 한 파일 책임이 흐려져서 분리했다.
"""

from auto_apply.ai.schemas import ResumeContentSchema, ResumeHighlight
from auto_apply.contracts.portfolio import PortfolioMap
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
    portfolio: PortfolioMap,
) -> AssembledResume:
    bullets_by_block: dict[str, list[ResumeHighlight]] = {
        b.block_id: b.bullets for b in content.blocks
    }

    career_order: list[str] = []
    career_blocks: dict[str, list[ResumeBlockView]] = {}
    career_meta: dict[str, tuple[str, str | None]] = {}
    projects: list[ResumeBlockView] = []

    for block in blocks:
        view = ResumeBlockView(
            title=block.title,
            period=block.period,
            bullets=_to_bullets(bullets_by_block.get(block.id, [])),
            tech_stack=block.tech_stack,
        )
        if block.kind == "career":
            if block.entity not in career_blocks:
                career_order.append(block.entity)
                career_blocks[block.entity] = []
                career_meta[block.entity] = (block.entity_label, block.entity_period)
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
        # LLM 이 고른 카테고리 라벨을 실제 파일명으로 바꾼다 — 매칭되는 게 없으면(라벨을
        # 안 골랐거나, 목록에 없는 걸 창작했으면) 그냥 비워둔다. Recipe 쪽 selector 는 이
        # 값이 비면 그 액션을 건너뛰도록 optional 로 짠다(domain/recipe_selector.py).
        portfolio_filename=portfolio.categories.get(content.job_category, ""),
    )


def used_fact_ids(content: ResumeContentSchema) -> list[str]:
    ids: set[str] = {fid for h in content.highlights for fid in h.fact_ids}
    ids |= {fid for h in content.ai_usage for fid in h.fact_ids}
    ids |= {fid for b in content.blocks for h in b.bullets for fid in h.fact_ids}
    return sorted(ids)
