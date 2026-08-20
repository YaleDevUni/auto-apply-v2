"""wanted 이력서 첨부파일 리스트 정리 (wanted-resume-list-cleanup-backlog).

Recipe 는 지원마다 새 파일명으로 이력서를 재생성해 업로드한다 — 과거 파일을 재사용하는
경로가 없어서, 지원(dry_run 포함) 1회 = 계정에 영구히 남는 고아 파일 1개다. 판정 로직은
`domain/resume_cleanup.py`(순수 함수) 참고 — 포트폴리오/사람이 올린 이력서는 이름이
고정돼 있어 여러 지원에 재사용되므로 자동 삭제 대상에서 제외된다.

`watchdog.py`/`schedule.py`처럼 Temporal 을 쓰는 운영 진입점이지만, 이 스크립트는 Temporal
자체가 필요 없다 — 되돌릴 수 없는 삭제를 durable 워크플로우로 감쌀 이유가 없고(§11.3 대상
아님), watchdog 처럼 상시 폴링할 이유도 없다(사람이 그때그때 검토하며 1회 실행). 기본은
dry-run(후보만 출력) — 실제 삭제는 `--yes` 가 있어야 한다(CLAUDE.md "되돌릴 수 없는
행위는 사람 승인 뒤에서만" — 여긴 텔레그램 승인 대신 명시적 CLI 플래그가 그 역할을 한다).
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from auto_apply.bootstrap import build_container
from auto_apply.config import load_settings
from auto_apply.domain.resume_cleanup import select_deletable

log = structlog.get_logger(__name__)


async def run(platform: str, *, min_age_hours: int, apply: bool) -> None:
    cfg = load_settings()
    container = build_container(cfg)
    manager = container.attachments.for_platform(platform)

    attachments = await manager.list_attachments()
    candidates = select_deletable(
        attachments, now=datetime.now(UTC), min_age=timedelta(hours=min_age_hours)
    )

    if not candidates:
        print(f"{platform}: 삭제 대상 없음 (전체 {len(attachments)}개 중 0개)")
        return

    print(f"{platform}: 삭제 대상 {len(candidates)}개 / 전체 {len(attachments)}개")
    for a in candidates:
        print(f"  - {a.title}  (updated_at={a.updated_at.isoformat()})")

    if not apply:
        print("dry-run — 실제로 지우려면 --yes 를 추가해라")
        return

    for a in candidates:
        await manager.delete_attachment(a.key)
        log.info("resume_cleanup.deleted", platform=platform, key=a.key, title=a.title)
    print(f"{len(candidates)}개 삭제 완료")


def main() -> None:
    parser = argparse.ArgumentParser(prog="resume-cleanup", description=__doc__)
    parser.add_argument("--platform", default="wanted")
    parser.add_argument(
        "--min-age-hours",
        type=int,
        default=2,
        help="이보다 최근에 올라온 파일은 아직 실행 중인 워크플로우가 쓰고 있을 수 있어 건너뛴다",
    )
    parser.add_argument("--yes", action="store_true", help="실제로 삭제한다 (기본은 dry-run)")
    args = parser.parse_args()
    asyncio.run(run(args.platform, min_age_hours=args.min_age_hours, apply=args.yes))


if __name__ == "__main__":
    main()
