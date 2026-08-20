"""wanted 이력서 첨부파일 정리 대상 판정 (순수 함수, wanted-resume-list-cleanup-backlog).

Recipe 는 지원마다 새 파일명(`res_<16-hex>.pdf`, `adapters/clock/system.UuidIdGen`)으로
이력서를 재생성해 업로드한다 — 과거에 올린 파일을 재사용하는 경로가 없다. 그래서 이 패턴에
맞는 파일은 지원 1회가 끝나는 순간 고아가 된다. 반대로 포트폴리오 파일과 사람이 손으로 올린
이력서는 이름이 고정돼 있고 여러 지원에 걸쳐 그 이름으로 다시 선택된다(`config/portfolio_map.yaml`,
Recipe 의 `profile.portfolio_filename`) — 이름이 패턴에 안 맞는 `application/pdf` 는 전부
보존 대상이다. `content_type == "wanted/resume"`(원티드 자체 이력서 빌더)는 이 프로젝트가
아예 만들지 않는 문서라 손대지 않는다.

application_id ↔ wanted 파일 사이의 상관관계를 DB에 남기지 않는다(`application_attempts` 는
resume 파일명을 기록하지 않는다, 실측 확인) — 그래서 "이미 지원 완료된 것만" 대신 age 기반
안전 버퍼로 "혹시 아직 실행 중인 워크플로우가 쓰고 있을 수 있는" 최근 파일을 보호한다.
"""

import re
from datetime import datetime, timedelta

from auto_apply.contracts.dto import ResumeAttachment

# UuidIdGen.new_id("res") 가 만드는 이름과 정확히 같은 모양이어야 한다 — 느슨하게 잡으면
# 사람이 올린 "res_report.pdf" 같은 파일까지 삼킬 수 있다.
_GENERATED_RESUME = re.compile(r"^res_[0-9a-f]{16}\.pdf$")

# Recipe selector 디버깅 중 실측용으로 직접 업로드했던 산출물 (recipe-debugging-workflow).
# 정규식 패턴에 안 걸리는 고정 파일명이라 따로 allowlist 한다.
KNOWN_TEST_TITLES = frozenset({"recipe-test-dummy.pdf"})

DEFAULT_MIN_AGE = timedelta(hours=2)


def is_deletable_title(title: str) -> bool:
    return bool(_GENERATED_RESUME.match(title)) or title in KNOWN_TEST_TITLES


def select_deletable(
    attachments: list[ResumeAttachment],
    *,
    now: datetime,
    min_age: timedelta = DEFAULT_MIN_AGE,
) -> list[ResumeAttachment]:
    """자동 생성 이력서 PDF + 알려진 테스트 산출물만, 그것도 `min_age` 보다 오래된 것만 고른다."""
    return [
        a
        for a in attachments
        if a.content_type == "application/pdf"
        and is_deletable_title(a.title)
        and now - a.updated_at >= min_age
    ]
